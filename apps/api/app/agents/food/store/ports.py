"""Food가 읽고 쓰는 데이터의 계약.

문서(docs/agents/data_model 계열)가 정한 테이블 모양을 그대로 따른다. 구현체는
DB·외부 API가 연결된 뒤에 붙인다 — 이번 커밋은 포트와 테스트·eval 용 InMemory
구현까지만 다룬다.

Food가 쓰는 것은 자기만 쓰고 읽는 `daycare_meal` 수정·삭제와 메뉴 카탈로그
(`MenuCatalogStore.put`) 둘이다. 영양 합계·구간은 저장하지 않고 판정할 때마다 원본
(`observation_food` · `daycare_meal`)에서 계산한다. 없는 급식을 새로 만드는
INSERT는 Food의 권한 밖으로, 식사 기록은 Memory, 급식 원본은 OCR 분석이 채운다.
메뉴 카탈로그는 캐시 미스 때 Food 요청 안에서 `MenuSource` 로 외부 조회하고(급식 · 영양
판정 · 집 식사 이름 모두), 결과를 받은 뒤 백엔드 어댑터가 짧게 저장한다. 외부 API 를
기다리는 동안 DB 트랜잭션을 쥐지 않는다(food_agent_own_table §1). DB 권한 방식은 백엔드가
정한다. 이름이 정확히 같은 결과만 넣고, 해석 실패는 넣지 않는다.

쓰기 포트는 호출 하나가 짧은 트랜잭션 하나로 바로 commit된다. 같은 run의 뒤에 오는
조회(다른 task 포함)가 그 결과를 읽어야 해서다. 조회 포트도 호출마다 짧은
세션으로 읽는다. Food는 run 내내 DB 연결을 쥐지 않는다.

`child_id` 를 갖는 기준은 둘 중 하나다 — ① Food 가 그 행을 쓰기(update·delete)까지 하는가,
② `suggestion_evidence` 의 근거 행이 될 수 있는가(`app/agents/common/refs.py` 의
`ChildRecordKind`). `FoodObservation`·`DaycareMealRow`·`Measurement` 는 실제 DDL 에도
`child_id NOT NULL` 이 있고 위 둘 중 하나 이상에 해당한다 — `daycare_meal` 은 Food 가 쓰는
행(①), `observation_food`·`child_growth_log` 는 근거 행(②)이라 서버가 "이 행이 정말 같은
아이 것인가"를 이 값으로 검증한다(`Ref` 주석과 같은 축). 급식 쓰기 포트(`update`·`delete`)도
①과 같은 이유로 `child_id` 를 받는다 — 아이 축 없이 `id` 만으로 쓰면 잘못된 id 하나가
남의 아이 급식 행을 고치거나 지울 수 있다.

`MenuCatalogRow`·`FoodDocRow`·`NutritionFacts` 는 아이와 무관한 공용 데이터(메뉴
카탈로그·참고 문서·영양성분)라 둘 다 해당하지 않는다. `SafetyEntry`(`health_safety`)도
`child_id` 가 없다 — Food 는 이 행을 쓰지 않고(①에 해당 안 함) `ChildRecordKind` 에도
없어 근거 행이 되지 않는다(②에도 해당 안 함). 안전 게이팅 경로는
`build_gate`가 `SafetyReader.food_safety(child_id=...)` 를 한 번 불러 그 결과를 같은 task 의
`FoodRunState.safety` 에만 두고 쓰므로, 아이가 섞일 자리 자체가 없다.

| 포트 | 연결 대상 |
| ChildProfileReader | Child_Profile — birth_date 만 |
| ConsentReader | consent(scope=child_health) 최신 행 |
| SafetyReader | health_safety — status='active' 행. kind 는 전부(allergy · chronic_disease · …) |
| GrowthLogReader | child_growth_log — 가장 최근 측정 |
| FoodMemoryReader | profile_affinity(domain=food) · observation_food |
| MenuCatalogStore | menu_catalog — 메뉴 카탈로그. 메뉴명(공개 DB · 급식표) → 식품코드·영양성분 |
| MenuSource | 식약처 등 외부 영양성분·레시피 조회 |
| DaycareMealStore | daycare_meal — 조회·수정·삭제 |
| FoodDocReader | food_doc — 이유식 단계 등 참고 문서 |
| SuggestionHistoryReader | 최근 제안한 menu_key (반복 회피용) |
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.rules.age import Stage

# 영양소 구간. 한 번 low 였던 항목은 EAR 의 1.1배를 넘어야 ok 로 돌아간다(히스테리시스,
# 계획서 "전체에 걸린 값"). app/rules/age.py 의 Band(연령대)와는 다른 축이라 이름을 겹치지
# 않게 NutrientBand 로 둔다. 판정 결과에만 쓰고 저장하지 않는다 — 전날 구간도 7일 전부터
# 하루씩 다시 판정해서 얻는다(영양소_계산_설계 §3).
NutrientBand = Literal["low", "ok", "high"]

# observation_food.confidence_source 값(memory/schemas/common.py 와 같은 값).
# 보호자 말은 Memory Agent 가 저장할 때 고르고, OCR 분석이 쓰는 관찰은 그 경로가 따로 정한다.
ConfidenceSource = Literal["institution_notice", "parent_direct", "parent_hedged", "parent_hearsay"]


class SafetyLookupError(Exception):
    """health_safety를 읽지 못함.

    기본값("제한 없음")으로 넘기지 않는다. 식단 추천은 모델을 부르지 않고 끝내고,
    급식 조회는 "알레르기 확인을 못 했어요" 를 명시한다 (루트 §2 · S6).
    """


class MenuSourceError(Exception):
    """외부 영양성분·레시피 API 호출 실패.

    결과가 0건인 것(정말 없음)과 다르다. 호출부는 이 예외를 카탈로그에 없는 것
    (캐시 미스)처럼 뭉개지 않는다.
    """


# health_safety 10/4 확정안(app/domains/safety/models.py)과 같은 값. Agent 마다 필요한 칸이
# 달라서 Food가 따로 둔다. unknown 은 없다 — 행이 없는 것이 unknown 이라 DB 에서 오지 않는다
SafetyKind = Literal["allergy", "chronic_disease", "behavioral", "environmental", "other_medical"]
SafetyStatus = Literal["active", "retracted", "none"]
SafetyCategory = Literal["food", "drug", "environment"]


@dataclass(frozen=True)
class SafetyEntry:
    """health_safety 한 행에서 필터에 필요한 것만 뽑아 모델에게 전달.

    `severity`는 싣지 않는다. 필터가 쓰지 않는다 — 보호자가 active 로 둔 알레르기는 검사
    등급(class_0 이어도)과 상관없이 거른다. (코드가 등급을 보고 낮추면 의료 판단)
    질환의 제한 식품은 chronic_restriction.yaml 매핑만 쓴다.
    """

    kind: SafetyKind
    label: str  # 예: 보호자가 적은 자유 입력
    status: SafetyStatus
    # kind='allergy' 만 갖는다. 빈 튜플은 분류 없음(NULL · '{}' 같은 뜻)이고 거른다
    category: tuple[SafetyCategory, ...] = ()


@dataclass(frozen=True)
class FoodObservation:
    """`observation_food` 한 행.

    `confidence_source` · `source_notice_id` 는 기관 식사가 `daycare_meal` 과 겹치는지
    가르는 데 쓴다. 그날 급식이 있으면 기관에서 온 관찰은 영양 합계에서 뺀다
    (영양소_계산_설계 §3 · N-10). 값은 저장하는 쪽이 정하고, 어댑터는 DB 의 같은 이름 칸을
    그대로 읽어 넘긴다.
    """

    id: UUID
    child_id: UUID
    observed_on: date
    subject: str
    amount_text: str | None
    polarity: int
    updated_at: datetime
    confidence_source: ConfidenceSource | None = None
    source_notice_id: UUID | None = None  # 알림장 · 기관 공지에서 온 기록이면 그 notice id


@dataclass(frozen=True)
class Measurement:
    """`child_growth_log` 가장 최근 측정 한 건."""

    child_id: UUID
    height_cm: float | None
    weight_kg: float | None
    measured_on: date


@dataclass(frozen=True)
class NutritionFacts:
    """외부 영양성분 DB 조회 결과 한 건."""

    food_code: str
    name: str
    category: str  # 식품 대분류. 식품군 매핑에 쓴다
    serving_g: float | None  # 성인 1인분
    # energy · protein · fat · carbohydrate · sugars · sodium · calcium · iron · saturated_fat
    per_100g: dict[str, float]
    source_version: str


@dataclass(frozen=True)
class MenuCatalogRow:
    """메뉴 카탈로그(`menu_catalog`) 한 행 — 메뉴명(공개 DB · 급식표) → 식품코드·영양성분."""

    menu_key: str
    display_name: str
    source: Literal["mfds_nutri", "mfds_recipe", "center_standard", "manual"]
    resolved: bool
    synced_at: datetime
    food_code: str | None = None
    serving_g: float | None = None
    nutrients: dict[str, float] = field(default_factory=dict)  # 내부 전용
    ingredients: tuple[str, ...] = ()  # 비면 "재료 모름"
    allergen_codes: frozenset[int] = frozenset()
    food_groups: frozenset[str] = frozenset()
    stage_min: Stage | None = None  # None 은 toddler로 본다
    source_version: str | None = None


DaycareSlot = Literal["lunch", "snack_am", "snack_pm"]


@dataclass(frozen=True)
class DaycareMealRow:
    """`daycare_meal` 한 행.

    급식표에 인쇄된 알레르기 번호는 싣지 않는다 — 기관이 아이의 알레르기를 관리하고 보호자에게
    미리 알린다. 위험 식품 표시는 메뉴 이름 · 재료로 `filter_food_safety` 가 한다.
    """

    id: UUID
    child_id: UUID
    serve_date: date
    meal_slot: DaycareSlot
    menu_keys: tuple[str, ...]
    amount_factor: float = 1.0
    amount_known: bool = False
    caregiver_checked: bool = False
    # 급식표 읽기(사진 OCR · 센터 배포 엑셀) 또는 지역 센터 표준식단. NEIS 는 쓰지 않는다
    origin: Literal["ocr", "center_standard"] = "ocr"


@dataclass(frozen=True)
class FoodDocRow:
    """`food_doc` 한 행. 참고 문서라 개인화 근거로 세지 않는다."""

    id: UUID
    row_type: str  # weaning_stage · weaning_ingredient · meal_pattern · nutrient_note · feeding_tip
    body: str
    texture: str | None
    allergen_codes: frozenset[int]
    written_at: datetime


class ChildProfileReader(Protocol):
    async def birth_date(self, *, child_id: UUID) -> date:
        """식이 단계는 이 값에서 코드가 계산한다 (app/rules/age.py)."""
        ...


class ConsentReader(Protocol):
    async def child_health_granted(self, *, child_id: UUID) -> bool:
        """consent(scope=child_health) 최신 행이 granted 인가."""
        ...


class SafetyReader(Protocol):
    async def food_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        """`status='active'` 행만 돌려준다. kind 는 전부다.

        데이터 모델의 "안전 조회는 항상 status='active'"와 같다. 백엔드 어댑터는 repository 의
        `list_active_safety`를 그대로 쓰면 된다. 행이 없는 것은 unknown 이라 빈 목록이 정상이다.
        읽기에 실패하면 SafetyLookupError. 빈 목록을 대신 돌려주지 않는다.
        """
        ...


class GrowthLogReader(Protocol):
    async def latest(self, *, child_id: UUID) -> Measurement | None:
        """가장 최근 측정 한 건. 기록이 없으면 None."""
        ...


class FoodMemoryReader(Protocol):
    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        """`profile_affinity`(domain=food). common/evidence.py 가 순위를 매긴다."""
        ...

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[FoodObservation]:
        """`observation_food` 구간 조회. `active` · `stand_alone` 행만 돌려준다.

        `memory_bridge.FOOD_STATUSES` 와 같은 기준이다. "잘못된 기록"(`inactive`)과 삭제한
        행은 근거로도, 영양 판정 합계로도 쓰지 않는다(영양소_계산_설계 §3). 출처 두 칸
        (`confidence_source` · `source_notice_id`)도 읽어 온다 — `FoodObservation` 참고.
        """
        ...


class MenuCatalogStore(Protocol):
    async def get(self, menu_key: str) -> MenuCatalogRow | None: ...
    async def get_many(self, menu_keys: tuple[str, ...]) -> dict[str, MenuCatalogRow]:
        """여러 메뉴를 조회 한 번으로 읽는다. 영양 판정이 14일 치 메뉴를 찾을 때 쓴다.

        카탈로그에 있는 행만 `menu_key` 를 키로 돌려준다. 없는 키(캐시 미스)는 빠진다.
        `get` 처럼 `resolved` 와 무관하게 있는 행은 다 돌려준다 — 해석 여부는 호출부가
        `resolved` 로 가른다(`all_resolved` 와 다른 점). 빈 튜플이면 빈 dict 다.
        어댑터는 `WHERE menu_key = ANY(:keys)` 한 문장이다.
        """
        ...

    async def put(self, row: MenuCatalogRow) -> None:
        """카탈로그에 없을 때만 넣는다 — 어댑터는 `ON CONFLICT (menu_key) DO NOTHING`.

        같은 메뉴를 두 run 이 동시에 채워도 한 행만 남고, 사람이 넣은 행(manual ·
        center_standard)을 덮지 않는다. 외부 조회 결과를 받은 뒤 짧은 트랜잭션 하나로 바로
        commit 된다. 있는 행을 고치거나 지우는 길은 포트에 없다. DB 권한 방식은 백엔드가
        정한다.

        해석 실패는 넣지 않는다 — 정말 없음도, `MenuSourceError`(외부 API 장애)도. 남기면
        보호자 말에서 나온 이름이 `child_id` 없이 전역 테이블에 남는다. 넣는 행은 외부 DB 에
        이름이 정확히 같은 결과뿐이라 `menu_key` 가 공개 DB 에 있는 이름이다
        (food_agent_own_table §1).
        """
        ...

    async def all_resolved(self) -> list[MenuCatalogRow]:
        """`resolved=True` 행만 돌려준다. 재료가 빈 행을 거르는 것은 후보 풀 생성의 몫이다."""
        ...


class MenuSource(Protocol):
    """외부 영양성분 · 레시피 조회. 실패하거나 정보가 부족하면 그 메뉴는 unresolved 로 둔다.

    `name`(정규화한 메뉴 이름)과 이름이 정확히 같은 결과만 돌려준다 — 비슷한 이름만 있으면
    None 이다(된장찌개를 물었는데 부대된장찌개를 주지 않는다). 받은 결과가 그대로 카탈로그에
    쌓여서다(food_agent_own_table §1).

    unresolved · 재료 없음은 알레르기 "확인 못 함"이다 — 안전한 것으로 보지 않는다
    (`tools/safety.py` 의 unchecked).
    """

    async def nutrition(self, *, name: str) -> NutritionFacts | None:
        """실패는 MenuSourceError. None 은 그 이름의 영양성분이 정말 없다는 뜻."""
        ...

    async def ingredients(self, *, name: str) -> tuple[str, ...] | None:
        """None = 레시피 없음."""
        ...


class DaycareMealStore(Protocol):
    async def rows(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[DaycareMealRow]: ...
    async def has_rows(self, *, child_id: UUID) -> bool: ...
    async def update(self, row: DaycareMealRow) -> None:
        """급식 수정 범위는 과거 30일과 미래 전부(계획서 "전체에 걸린 값").

        `row.child_id` 가 이미 아이 축을 들고 있다 — 어댑터는 `WHERE id = ? AND child_id = ?`
        로 갈 수 있다. 그래서 이 메서드는 `child_id` 를 따로 받지 않는다.

        호출 하나가 짧은 트랜잭션 하나로 바로 commit 된다. 같은 run의 읽는 task가 갱신된
        행을 본다. 행을 통째로 바꾸므로 같은 갱신을 두 번 적용해도 결과가 같다.
        """
        ...

    async def delete(self, *, child_id: UUID, row_ids: tuple[UUID, ...]) -> None:
        """`child_id` 가 다른 행은 지우지 않는다 — `row_ids` 만으로 지우면 다른 아이의
        급식 id 가 섞여 들어와도 그대로 지워진다.

        호출 하나가 짧은 트랜잭션 하나로 바로 commit 된다.
        """
        ...


class FoodDocReader(Protocol):
    async def search(
        self, *, stage: Stage, row_types: tuple[str, ...], keys: tuple[str, ...]
    ) -> list[FoodDocRow]: ...


class SuggestionHistoryReader(Protocol):
    async def recent_menu_keys(self, *, child_id: UUID, since: date) -> frozenset[str]: ...


@dataclass(frozen=True)
class FoodPorts:
    """Food Agent 가 요청 하나를 처리하는 동안 쥐는 포트 묶음.

    포트는 DB 연결을 쥐고 있지 않다. 조회는 호출마다 짧은 세션으로 읽어서 한 task 안에서도
    두 조회의 시점이 다를 수 있다. 그래서 안전 정보는 task 시작에 `build_gate` 가 한 번 읽어
    `FoodRunState.safety` 에 두고, 게이트와 필터가 그 값을 같이 쓴다.
    """

    profile: ChildProfileReader
    consent: ConsentReader
    safety: SafetyReader
    growth: GrowthLogReader
    memory: FoodMemoryReader
    catalog: MenuCatalogStore
    daycare: DaycareMealStore
    docs: FoodDocReader
    suggestions: SuggestionHistoryReader
    menu_source: MenuSource | None = None  # None 이면 카탈로그만 쓴다 (API 키 없음)
