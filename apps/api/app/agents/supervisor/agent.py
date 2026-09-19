"""Supervisor 진입점. LLM 을 한 번 부르고 결과를 검증한다.

검증에 실패하거나 LLM이 실패하면 재시도하지 않고 호출자(pipeline)가 Memory 단독으로 강등한다.
Memory는 원문 전체를 보므로 Supervisor가 없어도 기록은 남는다.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.agents.common.llm_client import LLMBadRequestError, LLMClient, LLMError
from app.agents.supervisor.prompt import build_system_prompt
from app.agents.supervisor.schemas import (
    ROUTE_TOOL_SPEC,
    ROUTE_TOOL_SPEC_STRICT,
    Segment,
    SupervisorOutput,
    validate_segments,
)

logger = logging.getLogger(__name__)

MAX_COMPLETION_TOKENS = 900

# 실패 코드
BAD_JSON = "BAD_JSON"  # arguments가 JSON 이 아니다
SCHEMA = "SCHEMA"  # 스키마/짝 검사 위반
NO_TOOL_CALL = "NO_TOOL_CALL"  # route를 부르지 않고 말로 답함
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"  # 인증/rate limit/네트워크/설정. detail에 예외 이름

# route를 강제로 부르게 한다. provider 가 거절하면 순서대로 물러선다
_FORCE_ROUTE: dict[str, Any] = {"type": "function", "function": {"name": "route"}}

# strict 모드에서는 누락 필드나 잘못된 enum 값을 provider 단계에서 거를 수 있음.
# 다만 선택 필드가 nullable이라 모델이 라벨 대신 null을 반환하는 경우가 있어 기본값은 비활성화.
# 테스트에서는 strict를 끈 쪽이 RC07 놀이 추천과 T19 알레르기 등록을 안정적으로 분류함.
# 스키마 오류는 조각 단위 검증과 _repair에서 처리하도록 함.
# 재검증: $env:SUPERVISOR_STRICT="1"
_USE_STRICT = os.getenv("SUPERVISOR_STRICT", "0") == "1"


def _attempts(*, strict: bool) -> tuple[tuple[dict[str, Any], Any], ...]:
    """스펙과 tool_choice 조합을 앞에서부터 시도한다. provider 가 거절하면 다음으로 물러선다."""
    fallbacks = (
        (ROUTE_TOOL_SPEC, _FORCE_ROUTE),
        (ROUTE_TOOL_SPEC, "required"),
        (ROUTE_TOOL_SPEC, "auto"),
    )
    return ((ROUTE_TOOL_SPEC_STRICT, _FORCE_ROUTE), *fallbacks) if strict else fallbacks


_ATTEMPTS = _attempts(strict=_USE_STRICT)


@dataclass
class SupervisorResult:
    output: SupervisorOutput | None  # 검증까지 통과했을 때만
    error: str | None = None  # 위 실패 코드 중 하나. output과 둘 중 하나만 찬다
    # 에러 발생지: 조각 번호·필드 경로, LLM 실패면 예외 이름만
    detail: str | None = None
    model_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    # keep_rejected=True 일 때만 찬다. 조각 원문이 들어 있으니 로그·저장에 쓰지 않는다
    rejected: SupervisorOutput | None = None
    # 스키마에서 걸리면 조각이 객체가 되지도 못한다. 무엇을 냈는지 보려면 원본이 필요하다
    rejected_raw: str | None = None
    dropped_segments: int = 0  # 못 쓰게 깨져서 버린 조각 수. 나머지로 계속 간다

    @property
    def ok(self) -> bool:
        return self.output is not None


async def run(
    raw_text: str,
    *,
    client: LLMClient | None = None,
    keep_rejected: bool = False,
    feedback: str | None = None,
) -> SupervisorResult:
    """발화 한 줄을 조각으로 나눈다. LLM 1회.

    keep_rejected 는 라이브 분기 확인(합성 케이스)에서만 켠다 — 버려진 조각을 눈으로 보려는 것이다.
    pipeline 은 켜지 않는다.

    feedback은 다시 나눌 때 붙인다. 앞 결과의 어느 조각이 처리되지 않았는지 pipeline이 적어준다.
    system 프롬프트는 그대로라 캐시가 살아 있고, 달라지는 건 user 메시지 한 개뿐
    """
    started = time.perf_counter()
    try:
        llm = client or LLMClient(role="supervisor")
    except LLMError as exc:
        # SUPERVISOR_* 설정 오류. 입력 실패로 만들지 않고 강등
        return _failed(LLM_UNAVAILABLE, 0, {}, started, detail=type(exc).__name__)
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": raw_text},
    ]
    if feedback:
        messages.append({"role": "user", "content": feedback})

    usage: dict[str, int] = {}
    calls = 0
    response = None
    for spec, tool_choice in _ATTEMPTS:
        try:
            calls += 1
            response = await llm.chat(
                messages=messages,
                tools=[spec],
                tool_choice=tool_choice,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
            break
        except LLMBadRequestError:
            continue
        except LLMError as exc:
            return _failed(LLM_UNAVAILABLE, calls, usage, started, detail=type(exc).__name__)

    if response is None:  # 모든 tool_choice 거절됨
        return _failed(LLM_UNAVAILABLE, calls, usage, started, detail=LLMBadRequestError.__name__)
    usage = dict(response.usage)

    tool_calls = getattr(response.message, "tool_calls", None) or []
    route = next((call for call in tool_calls if call.function.name == "route"), None)
    if route is None:
        return _failed(NO_TOOL_CALL, calls, usage, started)

    try:
        arguments = json.loads(route.function.arguments or "")
    except json.JSONDecodeError:
        return _failed(BAD_JSON, calls, usage, started)
    if not isinstance(arguments, dict):
        return _failed(BAD_JSON, calls, usage, started)

    output, broken = _parse_segments(arguments)
    if output is None:
        # 쓸 수 있는 조각이 하나도 없음 -> 필드 경로와 사유만 남김
        return _failed(
            SCHEMA,
            calls,
            usage,
            started,
            detail=" / ".join(broken),
            raw=json.dumps(arguments, ensure_ascii=False) if keep_rejected else None,
        )
    if broken:
        # 조각 하나가 깨졌다고 분리 전체를 버리지 않음
        logger.info("supervisor 조각 %d개를 버리고 계속 간다: %s", len(broken), broken)

    failures = validate_segments(raw_text, output)
    if failures:
        # 버린 조각의 원문은 Memory가 raw_text로 그대로 확인
        detail = "조각 " + ", ".join(f"{index}:{code}" for index, code in failures)
        bad = {index for index, _ in failures}
        kept = [s for i, s in enumerate(output.segments, start=1) if i not in bad]
        if not kept:
            return _failed(
                failures[0][1],
                calls,
                usage,
                started,
                detail=detail,
                rejected=output if keep_rejected else None,
            )
        logger.info("supervisor 조각 %d개를 버리고 계속 간다: %s", len(bad), detail)
        output = SupervisorOutput(segments=kept)
        broken = [*broken, *(f"조각 {index}:{code}" for index, code in failures)]

    result = SupervisorResult(
        output=output,
        model_calls=calls,
        usage=usage,
        latency_ms=_elapsed(started),
        dropped_segments=len(broken),
    )
    _log(result)
    return result


def _failed(
    code: str,
    calls: int,
    usage: dict[str, int],
    started: float,
    *,
    detail: str | None = None,
    rejected: SupervisorOutput | None = None,
    raw: str | None = None,
) -> SupervisorResult:
    result = SupervisorResult(
        output=None,
        error=code,
        detail=detail,
        model_calls=calls,
        usage=usage,
        latency_ms=_elapsed(started),
        rejected=rejected,
        rejected_raw=raw,
    )
    _log(result)
    return result


def _parse_segments(arguments: dict[str, Any]) -> tuple[SupervisorOutput | None, list[str]]:
    """조각을 하나씩 검증한다. 못 쓰는 조각만 버리고 나머지로 출력을 만든다."""
    raw_segments = arguments.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        return None, ["segments: 조각이 없다"]

    kept: list[Segment] = []
    broken: list[str] = []
    for index, raw in enumerate(raw_segments):
        try:
            kept.append(Segment.model_validate(raw))
        except ValidationError as exc:
            broken.append(f"segments.{index}: {_summarize(exc, limit=1)}")
    if not kept:
        return None, broken

    try:
        return SupervisorOutput.model_validate({**arguments, "segments": kept}), broken
    except ValidationError as exc:  # segments 밖의 필드가 틀림
        return None, [*broken, _summarize(exc)]


def _summarize(exc: ValidationError, limit: int = 3) -> str:
    """필드 경로와 사유만 짧게. 입력값(발화 원문)은 싣지 않는다."""
    parts = [
        f"{'.'.join(str(item) for item in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:limit]
    ]
    return " / ".join(parts)


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _log(result: SupervisorResult) -> None:
    """조각 원문은 남기지 않는다. 개수·라벨·실패 코드·지연만 남긴다."""
    segments = result.output.segments if result.output else []
    logger.info(
        "supervisor error=%s detail=%s segments=%d dropped=%d kinds=%s works=%s agents=%s "
        "food_tasks=%s model_calls=%d latency_ms=%d",
        result.error,
        result.detail,
        len(segments),
        result.dropped_segments,
        [segment.kind for segment in segments],
        [segment.work for segment in segments if segment.work],
        [segment.agent for segment in segments if segment.agent],
        [segment.food_task for segment in segments if segment.food_task],
        result.model_calls,
        result.latency_ms,
    )
