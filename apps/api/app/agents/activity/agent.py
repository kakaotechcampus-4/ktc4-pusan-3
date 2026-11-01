"""Activity Agent 진입점 — 사전 조회 + tool calling 루프 (설계 3-2).

순서
  ① 오늘 날씨를 먼저 판정해 `outdoor_ok` 를 정한다 — 장소 조회를 실내 종류로 좁힐지가 걸려 있다
  ② build_gate 로 월령 · 동의 · 안전 조회 · 위치를 모은다. 안전 조회가 실패했으면 모델 0회로 닫는다
  ③ 문서 행(예시)을 코드가 조회해 id 와 함께 프롬프트에 넣는다. 일반 추천은 이 id 를 근거로 단다
  ④ 모델이 조회 tool 을 부르고 마지막에 propose_activity_candidates 로 후보 3개를 낸다.
     거절되면 사유를 보고 고쳐 다시 낸다 — 같은 진입 안의 루프라 model_calls 가 늘지 않는다
  ⑤ 안전 필터로 빠져 3개가 안 되면 같은 대화에 이어 재호출 1회(model_calls=2). 처음 통과한 것을
     먼저 두고 빈자리만 채운다(`merge_drafts`). 그 뒤 check_count(exhausted=True) → count_notice.
     전부 빠졌는데 재호출도 후보를 못 내면 `failed` 다

저장은 하지 않는다. 결과의 suggestions 를 pipeline 이 저장한다 (공통규약 §3).
pipeline 에 등록돼 있다(#380). context 는 entrypoint 가 만든다 — 좌표 · 날씨 어댑터는 api 가 넘긴다.
"""

import logging
from collections.abc import Collection
from typing import Any

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.gating import checks_weather
from app.agents.activity.prompt import (
    build_system_prompt,
    example_ids,
    recall_message,
    request_message,
)
from app.agents.activity.readouts import CAUTION_NON_FOOD_ALLERGY, PLACE_AREA_UNKNOWN, READOUTS
from app.agents.activity.registry import closed_readout_key, execute_tool, specs_for, tools_for
from app.agents.activity.result import ToolResult
from app.agents.activity.rules import normalize_activity
from app.agents.activity.schemas.task import task_type_of
from app.agents.activity.tools.docs import search_activity_doc
from app.agents.activity.tools.outing import check_weather, days_of
from app.agents.activity.weather import (
    INDOOR_ONLY,
    OUTDOOR_ONLY,
    WeatherBrief,
)
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
from app.agents.common.tool_loop import run_tool_loop

logger = logging.getLogger(__name__)

AGENT = "activity"
OUTPUT_TOOL = "propose_activity_candidates"
# 대화 한 번의 왕복 상한. 기억 · 날씨 · 일정 · 장소 조회에 제출과 거절 뒤 재제출까지 들어가는 수
MAX_STEPS = 8
# 후보 3개에 칸이 많아 Memory 보다 넉넉히 둔다
MAX_COMPLETION_TOKENS = 2000


async def run(
    task: DomainTask, context: ActivityContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """놀이 추천 하나를 처리한다. 결과는 공통 `DomainAgentResult` 다."""
    task_type = task_type_of(task)
    # 장소 조회의 area · 출력 tool 의 requested_play 를 이 글과 대조한다 — 지어낸 값을 받지 않게
    context.state.request_texts = tuple(task.request_texts)
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
        context.ports.docs,
        months=gate.stage.months,
        query=" ".join(task.request_texts),
        # 같은 날 같은 아이는 같은 [예시] — 재호출도 같은 프롬프트다. 날이 바뀌면 바뀐다 (#343)
        rotation=f"{context.child_id}|{context.today.isoformat()}",
    )
    # 프롬프트에 실은 행만 근거로 달 수 있다. 재호출도 같은 system 프롬프트라 그대로 쓴다
    context.state.seen_docs = {row.id: row for row in examples}
    context.state.example_ids = example_ids(examples)
    system = build_system_prompt(context, examples, weather=weather)
    llm = client or LLMClient(role="activity")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": request_message(task.request_texts)},
    ]

    if not await _converse(llm, messages, context, allowed):
        return _failed(task_type.value, model_calls=1)

    picked = context.state.suggestions
    model_calls = 1
    if context.state.excluded and len(picked) < MAX_SUGGESTIONS:
        # 안전 필터로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다 (Tool_공통.md §5-2)
        model_calls = 2
        again = await _recall(llm, messages, task, context, allowed, picked)
        if again is None and not picked:
            # 처음 것이 전부 빠졌고 재호출도 후보를 못 냈다. "조건에 맞는 추천이 없어요" 가 아니라
            # 모델이 실패한 것이다 — 1~2개가 남았으면 그것을 낸다 (#359 리뷰)
            return _failed(task_type.value, model_calls=model_calls)
        picked = merge_drafts(picked, again or (), key=_same_activity)
        check_count(picked, exhausted=True)

    drafts = picked
    # 확인 문구(state.cautions)는 결과의 readouts 로 싣는다 — pipeline 은 for_task() 사본으로
    # Agent 를 돌리고 결과만 받아서, 여기 싣지 않으면 표시 없이 빠진다 (#380).
    # 위험 경고는 추천 하나에 붙는 문구라 그 초안의 warnings 칸에 이미 있다 (#402)
    context.state.suggestions = drafts
    notice = count_notice(len(drafts))
    attempted = checks_weather(gate)
    if context.state.area is not None:
        # 보호자가 말한 지역을 찾았다. 장소 조회가 보호자 위치의 판정을 비웠으니 오늘도 그 지역
        # 날씨로 다시 본다 — 위치가 없어도 그 지역 날씨는 물은 것이다
        if context.today not in context.state.weather:
            context.state.weather[context.today] = await check_weather(context, context.today)
        weather = context.state.weather[context.today]
        attempted = gate.data.weather
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value,
        status="degraded" if attempted and weather.unread else "completed",
        suggestions=drafts,
        readouts=(
            *(_caution(text) for text in context.state.cautions),
            *(READOUTS.render(key) for key in _weather_notices(weather, drafts, context)),
            *((READOUTS.render(PLACE_AREA_UNKNOWN),) if context.state.area_missed else ()),
            *((notice,) if notice else ()),
        ),
        model_calls=model_calls,
    )


def _weather_notices(
    weather: WeatherBrief, drafts: tuple[SuggestionDraft, ...], context: ActivityContext
) -> tuple[str, ...]:
    """화면에 붙일 날씨 안내 키 — 결과에 실제로 나간 추천의 날로 고른다 (#515 리뷰).

    - 바깥 활동이 없으면 오늘 판정의 안내다 — 왜 실내만인지("해가 져서" · "바깥 활동이 어려운
      날")를 알린다. "날씨는 확인하지 않았어요" · "확인하지 못했어요" 는 빼고(`OUTDOOR_ONLY`) —
      실내 놀이에 날씨 확인을 당부하면 군말이다.
    - 바깥 활동이 있으면 그 활동의 날 중 바깥이 열린 날의 안내다. "실내 놀이만 골랐어요" 류는
      뺀다 — 오늘 해가 졌어도 내일 공원이 나가면 그 말은 틀린다.
    """
    shown = {normalize_activity(d.content) for d in drafts}
    labels = dict.fromkeys(
        day for content, day in sorted(context.state.outdoor_drafts) if content in shown
    )
    if not labels:
        return tuple(key for key in weather.notices if key not in OUTDOOR_ONLY)
    keys: list[str] = []
    for label in labels:
        for day in days_of(label, context.today):
            brief = context.state.weather.get(day)
            if brief is not None and brief.outdoor_ok:
                keys.extend(key for key in brief.notices if key not in INDOOR_ONLY)
    return tuple(dict.fromkeys(keys))


def _caution(text: str) -> Readout:
    """알레르기 확인 문구 한 줄. 문구는 출력 tool 이 상수 템플릿(`caution.non_food_allergy`)으로
    이미 만들었다 — 모델을 거치지 않으므로 `authored_by="code"` 다.
    """
    return Readout(kind=READOUTS.get(CAUTION_NON_FOOD_ALLERGY).kind, body=text, authored_by="code")


def _same_activity(draft: SuggestionDraft) -> str:
    """재호출 결과에서 "같은 후보" — 최근 중복 검사와 같은 정규화다 (Tool_공통.md §5-2)."""
    return normalize_activity(draft.content)


def _failed(task_type: str, *, model_calls: int) -> DomainAgentResult:
    return DomainAgentResult(
        agent=AGENT, task_type=task_type, status="failed", model_calls=model_calls
    )


async def _recall(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    task: DomainTask,
    context: ActivityContext,
    allowed: Collection[str],
    first: tuple[SuggestionDraft, ...],
) -> tuple[SuggestionDraft, ...] | None:
    """재호출 1회. 같은 대화에 제외 목록 한 줄을 더해 이어 부른다 (Tool_공통.md §5-2).

    새 대화로 시작하면 모델이 기억 · 날씨 조회를 처음부터 다시 해 왕복이 늘고, pipeline 의 20초에
    걸리면 처음 통과한 것까지 잃는다. 이어 쓰면 앞의 조회 결과를 그대로 본다.
    후보를 못 내면 None 이다 — 0개 통과(전부 안전 필터로 빠짐)와 구별한다.
    """
    messages.append(
        {
            "role": "user",
            "content": recall_message(
                task.request_texts,
                chosen=[draft.content for draft in first],
                excluded=context.state.excluded,
            ),
        }
    )
    context.state.suggestions, context.state.excluded = (), ()
    if not await _converse(llm, messages, context, allowed):
        logger.warning("Activity 재호출이 후보를 내지 못했다 run_id=%s", context.run_id)
        return None
    return context.state.suggestions


async def _converse(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    context: ActivityContext,
    allowed: Collection[str],
) -> bool:
    """대화 한 번. 출력 tool 이 통과하면 True — 결과는 run state 에 있다.

    루프는 공통 `run_tool_loop` 다. `messages` 에 응답과 tool 결과를 이어 붙인다 — 재호출이 같은
    목록에 이어 쓴다. 한 응답에 tool 이 여러 개 오면 온 순서대로 다 실행하고, 깨진 인자는 실행하지
    않고 돌려준다. `tool_choice="required"` 라 말로 끝나지 않는다 — 라이브에서 auto 는 tool 을
    0/3 부르고 required 는 3/3 불렀다 (#320 리뷰). 그래도 말로 끝나거나 왕복 상한을 넘기면 False 다.
    """

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
        return await execute_tool(name, arguments, context, allowed=allowed)

    loop = await run_tool_loop(
        llm,
        messages,
        tools=specs_for(allowed),
        execute=execute,
        output_tools=(OUTPUT_TOOL,),
        max_steps=MAX_STEPS,
        tool_choice="required",
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    if not loop.completed:
        logger.warning(
            "Activity 가 후보를 내지 못했다 run_id=%s ended_by=%s", context.run_id, loop.ended_by
        )
    return loop.completed
