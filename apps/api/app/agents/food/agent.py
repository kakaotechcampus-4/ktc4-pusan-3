"""Food Agent 진입점.

지금은 라우팅 확인용 mock으로 LLM을 부르지 않고,
build_gate로 이번 run의 Gate(단계·안전 조회·급식 행 유무)를 계산해
받은 task 와 열릴 tool을 돌려주기만 하며,
결과는 Suggestion을 거치지 않는다.
"""

from dataclasses import dataclass
from typing import Literal

from app.agents.common.llm_client import LLMClient
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.registry import closed_readout_key, requires_safety_check, tools_for
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.task import FoodTask
from app.rules.age import Stage

# mock: 라우팅 확인용.
# unsupported_stage: closed_readout_key 가 있는 조합 — 연령 미지원(unsupported.*) ·
#   안전 조회 실패(blocked.safety) · 급식 행 없음(closed.no_daycare) 을 아직 한 값으로 처리.
FoodStatus = Literal["mock", "unsupported_stage"]


@dataclass(frozen=True)
class FoodAgentResult:
    task_type: FoodTaskType
    stage: Stage
    status: FoodStatus
    request_texts: tuple[str, ...]
    tools: tuple[str, ...]  # 이번 task에 모델에게 열리는 tool
    requires_safety_check: bool
    model_calls: int = 0  # Agent 진입 수 — 0(게이트 닫힘) · 1 · 2(안전 필터 후 재호출)


async def run(
    task: FoodTask, context: FoodContext, *, client: LLMClient | None = None
) -> FoodAgentResult:
    """Food Agent 진입점."""
    # DB 연결 후 처리 순서
    # 1. build_gate(context) 로 birth_date → LifeStage, 동의·안전 조회, 급식 행 유무를 계산.
    # 2. closed_readout_key 가 있으면 모델 호출 없이 그 코드 문구로 끝낸다.
    # 3. 식단 추천인 경우 후보 풀·영양 구간은 코드가 미리 준비한다.
    # 4. 모델에는 오늘 급식, 식이 단계, 필요한 안전 정보만 전달.
    #    food로 분류된 입력 외에 아이 이름이나 다른 도메인의 기록은 넘기지 않는다.
    # 5. task와 Gate에 맞는 tool 목록만 열어두고 tool calling을 수행.
    #    (실행 구조는 memory/agent.py와 동일)
    # 6. 규칙 기반 검사를 거쳐 응답.
    #    - 식단 추천: 금지 음식 필터링, 근거 확인, suggestion 형식 변환
    #    - 영양소 분석: 제안성 문장이나 불필요하게 정확한 수치가 포함됐는지 확인
    gate = await build_gate(context)
    key = closed_readout_key(task.task_type, gate)
    return FoodAgentResult(
        task_type=task.task_type,
        stage=gate.stage.stage,
        status="mock" if key is None else "unsupported_stage",
        request_texts=task.request_texts,
        tools=tools_for(task.task_type, gate),
        requires_safety_check=requires_safety_check(task.task_type),
    )
