"""Activity Agent 진입점 — 사전 조회 + tool calling 루프 (설계 3-2).

순서
  ① 오늘 날씨를 먼저 판정해 `outdoor_ok` 를 정한다 — 장소 조회를 실내 종류로 좁힐지가 걸려 있다
  ② build_gate 로 월령 · 동의 · 안전 조회 · 위치를 모은다. 안전 조회가 실패했으면 모델 0회로 닫는다
  ③ 문서 행(예시)을 코드가 조회해 프롬프트에 넣는다
  ④ 모델이 조회 tool 을 부르고 마지막에 propose_activity_candidates 로 후보 3개를 낸다.
     거절되면 사유를 보고 고쳐 다시 낸다 — 같은 진입 안의 루프라 model_calls 가 늘지 않는다
  ⑤ 안전 필터로 빠져 3개가 안 되면 재호출 1회(model_calls=2). 처음 통과한 것을 먼저 두고 빈자리만
     채운다(`merge_drafts`). 그 뒤 check_count(exhausted=True) → count_notice

저장은 하지 않는다. 결과의 suggestions 를 pipeline 이 저장한다 (공통규약 §3).
pipeline 에는 아직 연결하지 않았다 — Supervisor 라벨 · 추천 저장 콜백이 정해지면 붙인다.
"""

import json
import logging
from collections.abc import Collection
from typing import Any

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.prompt import build_system_prompt, recall_message, request_message
from app.agents.activity.readouts import READOUTS
from app.agents.activity.registry import closed_readout_key, execute_tool, specs_for, tools_for
from app.agents.activity.result import ErrorCode, fail
from app.agents.activity.schemas.task import task_type_of
from app.agents.activity.tools.docs import search_activity_doc
from app.agents.activity.tools.outing import check_weather
from app.agents.activity.weather import WEATHER_UNCHECKED, WeatherBrief
from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import (
    MAX_SUGGESTIONS,
    SuggestionDraft,
    check_count,
    count_notice,
    merge_drafts,
)

logger = logging.getLogger(__name__)

AGENT = "activity"
OUTPUT_TOOL = "propose_activity_candidates"
# 대화 한 번의 왕복 상한. 기억 · 날씨 · 일정 · 장소 조회에 제출과 거절 뒤 재제출까지 들어가는 수
MAX_STEPS = 8
# 후보 3개에 칸이 많아 Memory 보다 넉넉히 둔다
MAX_COMPLETION_TOKENS = 2000

_NUDGE = "말로 답하지 않는다. 조회를 마쳤으면 propose_activity_candidates 로 후보 3개를 낸다."
_BAD_JSON = "인자를 JSON 객체로 다시 보낸다."


async def run(
    task: DomainTask, context: ActivityContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """놀이 추천 하나를 처리한다. 결과는 공통 `DomainAgentResult` 다."""
    task_type = task_type_of(task)
    weather = await check_weather(context, context.today)
    gate = await build_gate(context, outdoor_ok=weather.outdoor_ok)
    context.state.gate = gate
    context.state.weather[context.today] = weather

    closed = closed_readout_key(task_type, gate)
    if closed is not None:
        return DomainAgentResult(
            agent=AGENT,
            task_type=task_type.value,
            status="blocked",
            readouts=(READOUTS.render(closed),),
        )

    allowed = tools_for(task_type, gate)
    examples = await search_activity_doc(
        context.ports.docs, months=gate.stage.months, query=" ".join(task.request_texts)
    )
    system = build_system_prompt(context, examples, weather=weather)
    llm = client or LLMClient(role="activity")

    if not await _converse(llm, system, request_message(task.request_texts), context, allowed):
        return DomainAgentResult(
            agent=AGENT, task_type=task_type.value, status="failed", model_calls=1
        )

    drafts, warnings = context.state.suggestions, context.state.warnings
    excluded = context.state.excluded
    model_calls = 1
    if excluded and len(drafts) < MAX_SUGGESTIONS:
        # 안전 필터로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다 (Tool_공통.md §5-2)
        model_calls = 2
        drafts, warnings = await _recall(llm, system, task, context, allowed, drafts, warnings)
        check_count(drafts, exhausted=True)

    context.state.suggestions, context.state.warnings = drafts, warnings
    notice = count_notice(len(drafts))
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value,
        status="degraded" if _weather_failed(context, weather) else "completed",
        suggestions=drafts,
        readouts=(*_weather_readouts(context, weather), *((notice,) if notice else ())),
        model_calls=model_calls,
    )


async def _recall(
    llm: LLMClient,
    system: str,
    task: DomainTask,
    context: ActivityContext,
    allowed: Collection[str],
    first: tuple[SuggestionDraft, ...],
    first_warnings: tuple[tuple[str, ...], ...],
) -> tuple[tuple[SuggestionDraft, ...], tuple[tuple[str, ...], ...]]:
    """재호출 1회. 처음 통과한 것을 먼저 두고 빈자리만 채운다. 경고 문구도 같은 순서로 따라간다."""
    message = recall_message(
        task.request_texts,
        chosen=[draft.content for draft in first],
        excluded=context.state.excluded,
    )
    context.state.suggestions, context.state.warnings, context.state.excluded = (), (), ()
    again: tuple[SuggestionDraft, ...] = ()
    again_warnings: tuple[tuple[str, ...], ...] = ()
    if await _converse(llm, system, message, context, allowed):
        again, again_warnings = context.state.suggestions, context.state.warnings
    else:
        logger.warning("Activity 재호출이 후보를 내지 못했다 run_id=%s", context.run_id)

    merged = merge_drafts(first, again)
    by_draft = dict(zip((*first, *again), (*first_warnings, *again_warnings), strict=True))
    return merged, tuple(by_draft[draft] for draft in merged)


async def _converse(
    llm: LLMClient,
    system: str,
    user: str,
    context: ActivityContext,
    allowed: Collection[str],
) -> bool:
    """대화 한 번. 출력 tool 이 통과하면 True — 결과는 run state 에 있다.

    한 응답에 tool 이 여러 개 오면 온 순서대로 전부 실행한다. 말로만 끝내면 한 번 다시 묻고,
    그래도 내지 않거나 왕복 상한을 넘기면 False 다.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    tools = specs_for(allowed)
    nudged = False
    for _ in range(MAX_STEPS):
        response = await llm.chat(
            messages=messages, tools=tools, max_completion_tokens=MAX_COMPLETION_TOKENS
        )
        calls = getattr(response.message, "tool_calls", None) or []
        if not calls:
            if nudged:
                break
            nudged = True
            messages.append({"role": "assistant", "content": response.message.content or ""})
            messages.append({"role": "user", "content": _NUDGE})
            continue

        messages.append(_assistant_message(response.message))
        proposed = False
        for call in calls:
            name = call.function.name
            arguments = _parse_arguments(call.function.arguments)
            if arguments is None:
                result = fail(None, name, ErrorCode.INVALID_ARGS, _BAD_JSON)
            else:
                result = await execute_tool(name, arguments, context, allowed=allowed)
            proposed = proposed or (name == OUTPUT_TOOL and result.success)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result.to_payload(), ensure_ascii=False),
                }
            )
        if proposed:
            return True
    logger.warning("Activity 가 후보를 내지 못했다 run_id=%s", context.run_id)
    return False


def _weather_failed(context: ActivityContext, weather: WeatherBrief) -> bool:
    """날씨를 불렀는데 못 읽었는가. 위치나 날씨 포트가 없어 부르지 않은 것은 실패가 아니다 —
    위치가 없으면 실내 전용으로 정상 동작한다 (4-3).
    """
    attempted = context.ports.weather is not None and context.location is not None
    return attempted and WEATHER_UNCHECKED in weather.notices


def _weather_readouts(context: ActivityContext, weather: WeatherBrief) -> tuple[Readout, ...]:
    """날씨 안내 문구. 부르지 않은 날(위치 없음)은 "날씨를 확인하지 못했어요" 를 붙이지 않는다."""
    attempted = context.ports.weather is not None and context.location is not None
    if not attempted:
        return ()
    return tuple(READOUTS.render(key) for key in weather.notices)


def _parse_arguments(raw: str | None) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _assistant_message(message: Any) -> dict[str, Any]:
    """모델 응답을 다음 요청에 그대로 돌려보낼 형태로. SDK 객체를 그대로 덤프하지 않는다."""
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in message.tool_calls
        ],
    }
