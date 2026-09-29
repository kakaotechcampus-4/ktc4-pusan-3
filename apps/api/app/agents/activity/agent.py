"""Activity Agent 진입점.

지금은 게이팅 확인용 mock으로 LLM·외부 API 를 부르지 않고,
받은 task 와 열릴 tool을 돌려주기만 하며, 결과는 Suggestion을 거치지 않는다.
파이프라인에는 아직 연결하지 않았다 — routing·pipeline 을 여러 도메인 Agent 용으로 바꾸는
공통 작업(공통_구현_계획 §5)과 같이 붙인다.
"""

from dataclasses import dataclass
from typing import Literal

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.registry import tools_for
from app.agents.activity.schemas.task import ActivityTaskType, task_type_of
from app.agents.common.llm_client import LLMClient
from app.agents.common.schemas.task import DomainTask
from app.rules.age import Stage

# mock: 게이팅 확인용. 모델·외부 API 를 부르지 않았다
# 실구현 때는 공통 DomainAgentResult 를 돌려준다 (completed / degraded / failed)
ActivityStatus = Literal["mock"]


@dataclass(frozen=True)
class ActivityAgentResult:
    task_type: ActivityTaskType
    stage: Stage  # 로그용. 게이팅은 stage 가 아니라 months 눈금으로 한다
    status: ActivityStatus
    request_texts: tuple[str, ...]
    tools: tuple[str, ...]  # 이번 task에 모델에게 열리는 tool
    model_calls: int = 0  # Agent 진입 수 — 1 · 2(안전 필터 후 재호출). 닫힌 조합이 없어 0은 없다


async def run(
    task: DomainTask, context: ActivityContext, *, client: LLMClient | None = None
) -> ActivityAgentResult:
    """Activity Agent 진입점."""
    # 실구현 처리 순서 (3-2)
    # 1. 날씨를 먼저 조회해 outdoor_ok 를 정한다. 실패하면 False — 실내만.
    #    tool 목록은 모델을 부르기 전에 확정되는데 장소 조회가 이 값에 걸려 있다.
    # 2. build_gate 로 월령·동의·안전 조회·위치를 Gate 하나에 모은다.
    # 3. search_activity_doc 으로 월령 슬라이스 [예시] 상위 5행을 가져온다.
    # 4. tools_for 가 연 tool 만 모델에게 보여주고 tool calling 루프를 돈다
    #    (실행 구조는 memory/agent.py 와 같다. 루프 왕복은 model_calls 가 아니라 steps).
    # 5. propose_activity_candidates 가 출력 검증 → SuggestionDraft 3개 → DomainAgentResult.
    #    안전 필터 뒤 3개 미만이면 재호출 1회 (model_calls=2).
    task_type = task_type_of(task)
    # 날씨 조회는 아직 없다. 조회 실패와 같게 본다 — 실내만 (D8)
    gate = await build_gate(context, outdoor_ok=False)
    context.state.gate = gate
    return ActivityAgentResult(
        task_type=task_type,
        stage=gate.stage.stage,
        status="mock",
        request_texts=task.request_texts,
        tools=tools_for(task_type, gate),
    )
