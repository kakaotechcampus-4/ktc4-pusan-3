"""Food Agent 진입점.

지금은 라우팅 확인용 mock으로 LLM·DB 를 부르지 않고,
받은 task 와 열릴 tool을 돌려주기만 하며,
결과는 Suggestion을 거치지 않는다.
"""

from dataclasses import dataclass
from typing import Literal

from app.agents.common.llm_client import LLMClient
from app.agents.food.context import FoodContext
from app.agents.food.registry import requires_safety_check, supports, tools_for
from app.agents.food.schemas.common import FeedingStage, FoodTaskType
from app.agents.food.schemas.task import FoodTask

# mock: 라우팅 확인용. 모델·DB 를 부르지 않았다
# unsupported_stage: 식이 단계에 없는 조합 (영아기+영양소 분석)
# 실구현 때 추가 — blocked(health_safety 조회 실패·미입력) / completed / failed
FoodStatus = Literal["mock", "unsupported_stage"]


@dataclass(frozen=True)
class FoodAgentResult:
    task_type: FoodTaskType
    stage: FeedingStage
    status: FoodStatus
    request_texts: tuple[str, ...]
    tools: tuple[str, ...]  # 이번 task에 모델에게 열리는 tool
    requires_safety_check: bool
    model_calls: int = 0  # LLMClient.chat() 호출 수(입력 run 의 done.model_calls에 더해짐)


async def run(
    task: FoodTask, context: FoodContext, *, client: LLMClient | None = None
) -> FoodAgentResult:
    """Food Agent 진입점."""
    # DB 연결 후 처리 순서
    # 1. context.profile.birth_date로 개월 수를 계산해 FeedingStage를 결정.
    #    (현재는 임시로 context.stage를 사용)
    # 2. 현재 식이 단계에서 지원하지 않는 작업이면 unsupported_stage로 종료.
    #    (예: 영아기의 영양소 분석)
    # 3. 식단 추천인 경우 context.safety.food_safety()를 먼저 확인.
    #    조회에 실패하거나 필수 정보가 없으면 모델 호출 없이 blocked 처리.
    # 4. 모델에는 오늘 급식, 식이 단계, 필요한 안전 정보만 전달.
    #    food로 분류된 입력 외에 아이 이름이나 다른 도메인의 기록은 넘기지 않는다.
    # 5. task와 stage에 맞는 tool 목록만 열어두고 tool calling을 수행.
    #    (실행 구조는 memory/agent.py와 동일)
    # 6. 규칙 기반 검사를 거쳐 응답.
    #    - 식단 추천: 금지 음식 필터링, 근거 확인, suggestion 형식 변환
    #    - 영양소 분석: 제안성 문장이나 불필요하게 정확한 수치가 포함됐는지 확인
    stage = context.stage
    return FoodAgentResult(
        task_type=task.task_type,
        stage=stage,
        status="mock" if supports(task.task_type, stage) else "unsupported_stage",
        request_texts=task.request_texts,
        tools=tools_for(task.task_type, stage),
        requires_safety_check=requires_safety_check(task.task_type),
    )
