"""Food tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 식이 단계)과 날짜 계산의 기준(now, timezone),
읽기·쓰기 포트를 한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.

식이 단계는 더 이상 context가 값으로 들고 있지 않는다. `build_gate()`가 매 run
`ports.profile.birth_date`에서 `LifeStage` 를 계산해 `Gate`로 넘긴다.
따라서 Food registry(`tools_for`·`closed_readout_key`)가 안전 조회·급식 행 유무까지
한 값(Gate)으로 판단한다.
"""

import logging
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
    DaycareLookupError,
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

logger = logging.getLogger(__name__)


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

    # 급식 표를 읽다 실패했다(`DaycareLookupError`). 참이면 이 task 는 급식을 다시 읽지 않는다 —
    # 한 task 안에서 여러 번 읽는데(가중치 · 영양 판정 · tool) 실패가 읽을 때마다 달라지면 안 되고,
    # 느리게 실패하는 읽기를 되풀이하면 20초 예산을 먹는다. `mark_daycare_unread` 만 쓴다
    daycare_unread: bool = False
    utterance: str = ""  # span 검증용. request_texts를 줄바꿈으로 이은 것
    # 보호자가 실제로 한 말 — 이번 입력 원문과 앞 턴의 원문. 앞 대화를 이어 묻는 말이면
    # `utterance` 는 Supervisor 가 해석한 문장이라 앞 추천 이름(AI 가 쓴 글)이 든다. "보호자가
    # 재료 · 메뉴를 정해 물었는가" 를 코드가 보는 곳(`said_texts`)은 이 칸을 읽는다
    said: tuple[str, ...] = ()
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
    # 이번 run 의 후보 풀(menu_key → 카탈로그 행)과 레시피북 전체 · 최근 추천한 menu_key.
    # 보호자가 정한 조건으로 생성 후보를 받을 때(#740) 풀 이름과 같은 생성은 그 레시피 행으로 받고,
    # 풀 밖 레시피(안전 필터로 막힘 · 최근 추천)와 같은 이름은 막힌 것으로 본다. 최근 추천한 이름은
    # 거절한다
    pool_rows: dict[str, MenuCatalogRow] = field(default_factory=dict)
    recipe_keys: frozenset[str] = frozenset()
    recent_keys: frozenset[str] = frozenset()
    # 요청 글에 이름 · 재료가 나와 샘플 앞에 넣은 레시피(조건 맞는 레시피)의 menu_key. 생성 후보
    # 자리는 3 - (샘플 안에 든 이 수)다. 요리 종류 조건이면 세지 않는다
    requested_keys: frozenset[str] = frozenset()
    # 조건 불일치(`REQUEST_MISMATCH`)로 거절한 횟수. 모델이 요리 이름을 재료로 적는 등 조건을
    # 못 맞추면
    # 거절 루프에 갇혀 0개로 끝난다 — 두 번 거절한 뒤에는 조건 없는 요청으로 본다
    request_misses: int = 0
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

    def said_texts(self) -> tuple[str, ...]:
        """보호자가 실제로 한 말. `said` 가 비어 있으면(직접 채운 state) `utterance` 다."""
        return self.said or tuple(self.utterance.splitlines())


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


def mark_daycare_unread(context: FoodContext, *, at: str) -> None:
    """급식 표를 못 읽었다고 이 task 에 표시한다. 로그에는 run_id 와 자리 이름만 싣는다."""
    context.state.daycare_unread = True
    logger.warning("Food 급식 읽기 실패 run_id=%s at=%s", context.run_id, at)


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

    # 급식 표를 못 읽는 것은 안전 조회 실패와 다르다 — 식단 추천 · 영양소 분석을 닫지 않는다.
    # "없음" 으로 바꾸지도 않는다(`daycare_unread`). 급식 조회 · 고치기만 못 읽음 문구로 닫힌다
    try:
        daycare_meal = await ports.daycare.has_rows(child_id=context.child_id)
    except DaycareLookupError:
        mark_daycare_unread(context, at="gate")
        daycare_meal = False

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=basis.ok,
        data=DataReady(daycare_meal=daycare_meal, daycare_unread=context.state.daycare_unread),
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
