"""Food tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 식이 단계)과 날짜 계산의 기준(now, timezone),
읽기·쓰기 포트를 한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.

식이 단계는 더 이상 context가 값으로 들고 있지 않는다. `build_gate()`가 매 run
`ports.profile.birth_date`에서 `LifeStage` 를 계산해 `Gate`로 넘긴다.
따라서 Food registry(`tools_for`·`closed_readout_key`)가 안전 조회·급식 행 유무까지
한 값(Gate)으로 판단한다.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.common.gate import DataReady, Gate, SafetyState
from app.agents.food.store.ports import FoodPorts, SafetyLookupError
from app.rules.age import life_stage


@dataclass
class FoodRunState:
    """run 하나 동안만 사는 값. tool 이 읽고 쓴다.

    출력 검증(`propose_meal_candidates` 등)이 "이번 run 에서 실제로 조회·샘플링한
    것인가"를 확인할 때 쓴다 — 모델이 근거·후보를 지어내는 것을 막는 자리다.
    """

    utterance: str = ""  # span 검증용. request_texts를 줄바꿈으로 이은 것
    seen_refs: set[tuple[str, UUID]] = field(default_factory=set)  # 조회 tool이 돌려준 근거
    sampled_keys: tuple[str, ...] = ()  # 모델에게 보여 준 후보
    blocked_keys: set[str] = field(default_factory=set)
    basis_keys: set[str] = field(default_factory=set)  # compare_diet_balance가 돌려준 항목
    gate: Gate | None = None


@dataclass(frozen=True)
class FoodContext:
    child_id: UUID
    run_id: str
    now: datetime  # timezone이 붙은 현재 시각
    timezone: tzinfo
    ports: FoodPorts
    state: FoodRunState = field(default_factory=FoodRunState)

    @property
    def today(self) -> date:
        return today_of(self.now, self.timezone)

    def for_task(self) -> "FoodContext":
        """task 하나 몫의 context. run state만 새로 만든다.

        한 run에 Food task가 둘이면(식단 추천 + 영양소 분석) pipeline이 동시에 돌린다.
        """
        return replace(self, state=FoodRunState())


async def build_gate(context: FoodContext) -> Gate:
    """이번 run의 Gate(registry의 `tools_for`·`closed_readout_key`입력).

    - birth_date → `life_stage()`로 `LifeStage`를 계산한다.
    - 동의(`consent_child_health`)가 없으면 `health_safety`를 아예 읽지 않는다.
      읽을 것이 없는 상태라 allergy_states가 빈 튜플.
    - 동의가 있는데 조회가 실패하면(`SafetyLookupError`) `safety_ok=False`가 되고 이 값만
      식단 추천을 닫는다. 실패를 빈 목록으로 숨기지 않는다.
    - `DataReady.daycare_meal`은 `has_rows()` 원본
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    stage = life_stage(birth_date, context.today)
    consent = await ports.consent.child_health_granted(child_id=context.child_id)

    safety_ok = True
    allergy_states: tuple[SafetyState, ...] = ()
    if consent:
        try:
            entries = await ports.safety.food_safety(child_id=context.child_id)
        except SafetyLookupError:
            safety_ok = False
        else:
            allergy_states = tuple(entry.state for entry in entries if entry.kind == "allergy")

    daycare_meal = await ports.daycare.has_rows(child_id=context.child_id)

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=safety_ok,
        allergy_states=allergy_states,
        data=DataReady(daycare_meal=daycare_meal),
    )
