"""Supervisor 진입점. LLM 을 한 번 부르고 결과를 검증한다.

검증에 실패하거나 LLM이 실패하면 재시도하지 않고 호출자(pipeline)가 Memory 단독으로 강등한다.
Memory는 원문 전체를 보므로 Supervisor가 없어도 기록은 남는다.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.agents.common.llm_client import LLMBadRequestError, LLMClient, LLMError
from app.agents.supervisor.prompt import build_system_prompt
from app.agents.supervisor.schemas import (
    ROUTE_TOOL_SPEC,
    SupervisorOutput,
    validate_segments,
)

logger = logging.getLogger(__name__)

MAX_COMPLETION_TOKENS = 900

# 실패 코드
BAD_JSON = "BAD_JSON"  # arguments가 JSON 이 아니다
SCHEMA = "SCHEMA"  # 스키마/짝 검사 위반
NO_TOOL_CALL = "NO_TOOL_CALL"  # route를 부르지 않고 말로 답함
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"  # 인증/rate limit/네트워크

# route를 강제로 부르게 한다. provider 가 dict를 거절하면 순서대로 물러선다
_FORCE_ROUTE: dict[str, Any] = {"type": "function", "function": {"name": "route"}}
_TOOL_CHOICE_FALLBACKS: tuple[str, ...] = ("required", "auto")


@dataclass
class SupervisorResult:
    output: SupervisorOutput | None  # 검증까지 통과했을 때만
    error: str | None = None  # 위 실패 코드 중 하나. output과 둘 중 하나만 찬다
    detail: str | None = None  # 어디가 걸렸는지 — 조각 번호·필드 경로만. 값은 담지 않는다
    model_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    # keep_rejected=True 일 때만 찬다. 조각 원문이 들어 있으니 로그·저장에 쓰지 않는다
    # (합성 케이스로 프롬프트를 고칠 때 콘솔에서 보려고 둔 자리다)
    rejected: SupervisorOutput | None = None

    @property
    def ok(self) -> bool:
        return self.output is not None


async def run(
    raw_text: str, *, client: LLMClient | None = None, keep_rejected: bool = False
) -> SupervisorResult:
    """발화 한 줄을 조각으로 나눈다. LLM 1회.

    keep_rejected 는 라이브 분기 확인(합성 케이스)에서만 켠다 — 버려진 조각을 눈으로 보려는 것이다.
    pipeline 은 켜지 않는다.
    """
    llm = client or LLMClient(role="supervisor")
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": raw_text},
    ]

    started = time.perf_counter()
    usage: dict[str, int] = {}
    calls = 0
    response = None
    for tool_choice in (_FORCE_ROUTE, *_TOOL_CHOICE_FALLBACKS):
        try:
            calls += 1
            response = await llm.chat(
                messages=messages,
                tools=[ROUTE_TOOL_SPEC],
                tool_choice=tool_choice,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
            break
        except LLMBadRequestError:
            continue
        except LLMError:
            return _failed(LLM_UNAVAILABLE, calls, usage, started)

    if response is None:  # 모든 tool_choice 거절됨
        return _failed(LLM_UNAVAILABLE, calls, usage, started)
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

    try:
        output = SupervisorOutput.model_validate(arguments)
    except ValidationError as exc:
        # 필드 경로와 사유만. 값에는 발화 원문이 들어 있다
        return _failed(SCHEMA, calls, usage, started, detail=_summarize(exc))

    failures = validate_segments(raw_text, output)
    if failures:
        detail = "조각 " + ", ".join(f"{index}:{code}" for index, code in failures)
        return _failed(
            failures[0][1],
            calls,
            usage,
            started,
            detail=detail,
            rejected=output if keep_rejected else None,
        )

    result = SupervisorResult(
        output=output,
        model_calls=calls,
        usage=usage,
        latency_ms=_elapsed(started),
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
) -> SupervisorResult:
    result = SupervisorResult(
        output=None,
        error=code,
        detail=detail,
        model_calls=calls,
        usage=usage,
        latency_ms=_elapsed(started),
        rejected=rejected,
    )
    _log(result)
    return result


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
        "supervisor error=%s detail=%s segments=%d kinds=%s works=%s agents=%s food_tasks=%s "
        "model_calls=%d latency_ms=%d",
        result.error,
        result.detail,
        len(segments),
        [segment.kind for segment in segments],
        [segment.work for segment in segments if segment.work],
        [segment.agent for segment in segments if segment.agent],
        [segment.food_task for segment in segments if segment.food_task],
        result.model_calls,
        result.latency_ms,
    )
