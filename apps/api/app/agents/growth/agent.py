"""Growth Agent 진입점.

`growth_review` 는 모델을 부르지 않아서 끝까지 동작한다 — 측정을 읽어 판정 없이 정리한 readout 을
돌려준다. 나머지 세 라벨은 게이트와 닫힘 판정, 루틴 요청의 의료 처치 · 증상 닫힘까지만 하고
받은 task 와 열릴 tool 을 돌려주는 mock 이다. 모델 tool 루프 · 프롬프트는 모델 경로에서 붙인다.
파이프라인에는 아직 연결하지 않았다(TODO).
"""

from dataclasses import dataclass
from typing import Literal

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.context import GrowthContext, build_gate
from app.agents.growth.readouts import READOUTS
from app.agents.growth.registry import closed_readout_key, tools_for
from app.agents.growth.schemas.task import GrowthTaskType, task_type_of
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.delta import asks_judgement, compute_growth_delta
from app.rules.age import Stage

# completed: 모델 없이 끝까지 동작했다 (growth_review)
# blocked: 게이트 · 라벨 규칙이 닫았다. 코드 문구만 나간다 (모델 0회)
# mock: 모델 경로가 아직 없다. 게이팅 확인용 — 열릴 tool 만 돌려준다
GrowthStatus = Literal["completed", "blocked", "mock"]


@dataclass(frozen=True)
class GrowthAgentResult:
    task_type: GrowthTaskType
    stage: Stage  # 로그용. 게이팅은 stage 가 아니라 months 눈금으로 한다
    status: GrowthStatus
    request_texts: tuple[str, ...]
    tools: tuple[str, ...]  # 이번 task에 모델에게 열리는 tool. 닫혔거나 모델 없는 라벨이면 빈 튜플
    readouts: tuple[Readout, ...] = ()  # 코드가 만든 문구 — 닫힘 안내 · 성장 추이
    needs_observation: tuple[str, ...] = ()  # 되묻기 한 줄 (한 번에 하나)
    model_calls: int = 0  # Agent 진입 수 — 0(닫힘 · growth_review) · 1 · 2(안전 필터 후 재호출)
    agent: Literal["growth"] = "growth"  # pipeline 이 DomainOutcome 으로 읽는다


async def run(
    task: DomainTask, context: GrowthContext, *, client: LLMClient | None = None
) -> GrowthAgentResult:
    """Growth Agent 진입점."""
    # TODO: 모델 경로가 붙었을 때의 처리 순서
    # 1. build_gate 로 월령 · 동의 · 안전 조회를 Gate 하나에 모은다.
    #    닫혔으면 여기서 끝낸다 — 모델 0회.
    # 2. 루틴이면 요청 문장이 의료 처치 · 증상 목록에 걸리는지 본다. 걸리면 닫는다 — 모델 0회.
    # 3. 코드가 search_growth_doc 을 불러 [예시] 구획을 만든다. 모델에게 문서 검색 tool 은 없다.
    # 4. tools_for 가 연 tool 만 모델에게 보여주고 tool calling 루프를 돈다.
    # 5. 출력 tool 이 안전 필터 → 금지 표현 → 근거 검증을 거쳐 SuggestionDraft 3개로 바꾼다.
    task_type = task_type_of(task)
    gate = await build_gate(context, task_type)
    context.state.gate = gate
    stage = gate.stage.stage

    key = closed_readout_key(task_type, gate)
    if key is None and task_type is GrowthTaskType.ROUTINE_COACHING:
        key = closure_key(task.request_texts)
    if key is not None:
        return GrowthAgentResult(
            task_type=task_type,
            stage=stage,
            status="blocked",
            request_texts=task.request_texts,
            tools=(),
            readouts=(READOUTS.render(key),),
        )

    if task_type is GrowthTaskType.GROWTH_REVIEW:
        birth_date = context.state.birth_date
        assert birth_date is not None  # build_gate 가 채운다
        readout = compute_growth_delta(
            context.state.measurements,
            birth_date=birth_date,
            asks_judgement=asks_judgement(task.request_texts),
        )
        return GrowthAgentResult(
            task_type=task_type,
            stage=stage,
            status="completed",
            request_texts=task.request_texts,
            tools=(),
            readouts=(readout,),
        )

    return GrowthAgentResult(
        task_type=task_type,
        stage=stage,
        status="mock",
        request_texts=task.request_texts,
        tools=tools_for(task_type, gate),
    )
