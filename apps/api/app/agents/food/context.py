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
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

from app.agents.common.age import life_stage
from app.agents.common.datetime_rules import today_of
from app.agents.common.evidence import RankedEvidence
from app.agents.common.gate import DataReady, Gate, SafetyBasis, read_safety_basis
from app.agents.common.readout import Readout
from app.agents.common.suggestion import SuggestionDraft
from app.agents.food.schemas.common import MealSlot
from app.agents.food.store.ports import (
    FoodDocRow,
    FoodPorts,
    MenuCatalogRow,
    SafetyEntry,
    UnwiredSafety,
)

if TYPE_CHECKING:
    # nutrition_eval.py 가 FoodContext 를 쓰므로 런타임에 여기서 가져오면 순환 임포트다
    # (설계 §3 ⑤ — 판정 결과는 run state 에만 둔다)
    from app.agents.food.nutrition_eval import NutrientVerdict


@dataclass(frozen=True)
class MealPick:
    """출력 검증을 통과한 추천 하나와 그 후보의 `menu_key` · 끼니.

    `SuggestionDraft` 에는 메뉴 키가 없다. 안전 필터 뒤 재호출 결과를 합칠 때 같은 메뉴를 `menu_key`
    로 가르려고 짝으로 둔다(`common/suggestion.merge_drafts`).
    """

    draft: SuggestionDraft
    menu_key: str
    meal_slot: MealSlot


@dataclass
class FoodRunState:
    """run 하나 동안만 사는 값. tool 이 읽고 쓴다.

    출력 검증(`propose_meal_candidates` 등)이 "이번 run 에서 실제로 조회·샘플링한
    것인가"를 확인할 때 쓴다 — 모델이 근거·후보를 지어내는 것을 막는 자리다.
    """

    utterance: str = ""  # span 검증용. request_texts를 줄바꿈으로 이은 것
    seen_refs: set[tuple[str, UUID]] = field(default_factory=set)  # 조회 tool이 돌려준 근거
    # search_food_memory 가 돌려준 근거(id → 순위 매긴 근거). 출력 tool 이 이 표 밖 id 를 인용한
    # 후보를 거절하고(`common/evidence.unseen`), 기피 근거를 이 표로 찾는다
    seen_evidence: dict[UUID, RankedEvidence] = field(default_factory=dict)
    # 식단 추천이 모델을 부르기 전에 기본 기억 검색을 했는가(`prefetch_food_memory`). 0건이어도 참.
    # 모델이 도구로 다시 찾은 근거와 가르려고 선조회로 보여 준 id 를 따로 둔다.
    # 출력 검증은 계속 `seen_evidence` 만 믿는다
    memory_prefetched: bool = False
    prefetched_evidence_ids: set[UUID] = field(default_factory=set)
    # 모델이 `search_food_memory` 를 부른 횟수. 선조회는 세지 않는다 — 품질 로그용
    memory_search_calls: int = 0
    # 이번 run 에서 모델에게 보여 준 문서 행([예시] · guide_weaning_stage). 인용하면 `food_doc`
    # 근거가 되고 개인화로 세지 않는다
    seen_docs: dict[UUID, FoodDocRow] = field(default_factory=dict)
    # 모델에게 보여 준 후보(menu_key → 카탈로그 행). 출력 tool 이 이 표 밖 menu_key 를 거절하고,
    # 이름 · 재료는 이 행에서 온다 — 모델이 쓰지 않는다
    sampled: dict[str, MenuCatalogRow] = field(default_factory=dict)
    # 사후 안전 필터에 걸려 풀에서 빼야 하는 menu_key. 사유는 모델에게도 로그에도 싣지 않는다
    blocked_keys: set[str] = field(default_factory=set)
    # 더 채울 길이 없다 — 후보 풀이 처음부터 3개 미만이거나 안전 필터 뒤 재호출을 이미 했다.
    # run() 이 정한다. 참이면 3개 미만(0개 포함)도 통과한다(`common/suggestion.check_count`)
    exhausted: bool = False
    # propose_meal_candidates 를 통과한 추천. 재호출 결과와 합칠 때 menu_key 로 같은 추천을 가른다
    picks: tuple["MealPick", ...] = ()
    # 개수 안내(`suggestion.partial` · `pool.empty`). 3개를 다 채우면 없다
    notice: Readout | None = None
    # 도구가 직접 정한 화면 문구(급식 갱신 · 삭제의 "바꿔 뒀어요" · 되묻기 · 알레르기 경고). 코드가
    # 만든 문구라 모델 입력에는 넣지 않는다 — 아이의 알레르기 이름이 들어갈 수 있다. 쓰기가 끝난
    # 뒤에만 채운다. run() 이 결과로 옮긴다
    readouts: tuple[Readout, ...] = ()
    basis_keys: set[str] = field(default_factory=set)  # compare_diet_balance가 돌려준 항목
    # compare_diet_balance 가 advice_for 로 뺀 식품 이름 전부(여러 항목분 합침). 알레르기 때문에
    # 안내에서 뺀 식품이라 report_nutrient_analysis 의 check_summary 가 요약 문장에 남았는지 본다
    removed_foods: tuple[str, ...] = ()
    # evaluate_nutrient_bands 가 담는 판정 결과. 저장 · 로그 없음 — run state 에만 (설계 §3 ⑤)
    nutrient_verdict: "NutrientVerdict | None" = None
    gate: Gate | None = None
    # build_gate가 task 시작에 한 번 읽은 health_safety
    # () = 동의가 없어 읽지 않음(거를 것 없음), None = 조회 실패("알레르기 확인 못 함")
    safety: tuple[SafetyEntry, ...] | None = None


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
      읽을 것이 없는 상태라 `context.state.safety` 가 빈 튜플이다.
    - 동의가 있는데 조회가 실패하면(`SafetyLookupError`) `safety_ok=False`가 되고 이 값만
      식단 추천을 닫는다. 실패를 빈 목록으로 숨기지 않는다 — 판정은 공용
      `read_safety_basis()`
    - 안전 포트가 `UnwiredSafety`(어댑터 연결 전)면 동의와 상관없이 `safety_ok=False` 다.
      읽지도 않는다.
    - `DataReady.daycare_meal`은 `has_rows()` 원본
    - 읽은 안전 정보는 `context.state.safety`에 남긴다. 이 task의 필터는 전부 이 값을 쓴다.
    - `Gate.allergy_states`는 채우지 않는다. 포트가 active 행만 주고, 확인 안 된 알레르기
      (unknown)는 행이 없는 것이라 이 칸으로 셀 수 없다. 그 안내는 추천을 승인할 때
      `suggestion.allergens` 와 `health_safety`를 대조해서 한다.
      이 칸은 Activity도 쓰는 공통 칸이라 지우지 않는다.
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    stage = life_stage(birth_date, context.today)
    consent = await ports.consent.child_health_granted(child_id=context.child_id)

    if isinstance(ports.safety, UnwiredSafety):
        # 어댑터가 붙기 전이다. 동의가 없어도 닫아야 해서 동의를 보지 않는다 — 읽지도 않는다 (#372)
        basis: SafetyBasis[SafetyEntry] = SafetyBasis(entries=None)
    else:
        basis = await read_safety_basis(
            partial(ports.safety.food_safety, child_id=context.child_id), consent=consent
        )
    context.state.safety = basis.entries

    daycare_meal = await ports.daycare.has_rows(child_id=context.child_id)

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=basis.ok,
        data=DataReady(daycare_meal=daycare_meal),
    )


def require_safety(context: FoodContext) -> tuple[SafetyEntry, ...]:
    """이번 run 이 읽은 health_safety. None(조회 실패)은 빈 목록으로 바꾸지 않는다.

    `build_gate` 가 task 시작에 채운다 — 동의가 없으면 `()`(거를 것 없음), 조회에 실패하면
    None 이다.
    None 이면 식단 추천 · 영양소 분석은 `blocked.safety` 로 닫혀 여기까지 오지 않는다. 오면
    `build_gate` 를 거치지 않은 호출이라는 뜻이다. 알레르기를 거르지 못한 채 안내를 만들면
    안 된다(루트 §2).
    """
    entries = context.state.safety
    if entries is None:
        raise RuntimeError("안전 정보를 읽지 못한 채로 알레르기를 거를 수 없다")
    return entries
