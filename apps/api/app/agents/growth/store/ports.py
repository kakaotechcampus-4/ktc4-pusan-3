"""Growth가 읽는 데이터의 계약.

구현체는 DB·외부 API가 연결된 뒤에 붙인다.
Growth는 쓰기 포트가 없다 — 관찰은 Memory가, 추천 저장은 주입된 writer가 쓴다.

생일 · 동의 · 알레르기 포트(`ChildProfileReader` · `ConsentReader` · `SafetyLookupError`)는 Food ·
Activity 와 같은 이름 · 시그니처의 사본이다. 도메인 Agent 공통 포트(`common/ports.py`)를 만들 때
import 만 바꾸면 되게 일부러 같게 두었다 — 그 전에 여기서 `common/ports.py` 를 먼저 만들면 같은
새 파일이 양쪽에 생겨 충돌한다. 알레르기를 읽는 메서드 이름만 Agent 마다 다르다
(`food_safety` · `activity_safety` · `growth_safety`).

| 포트 | 연결 대상 |
| ChildProfileReader | Child_Profile — birth_date 만 |
| ConsentReader | consent(scope=child_health) 최신 행 |
| SafetyReader | health_safety — allergy · environmental 만. 교육 활동만 읽는다 |
| GrowthLogReader | child_growth_log — 측정 전부 (Food 의 최근 1건과 다르다) |
| GrowthMemoryReader | profile_affinity(education · activity) · 교육 · 루틴 · 놀이 관찰 |
| NoticeReader | notice — 기관 공지가 있는가 (없어도 추천은 동작) |
| GrowthDocReader | growth_doc — 월령 슬라이스 + 의미 검색, 자립 단계 사슬 통째로 |
| BookSource | 도서 검색 — book_catalog 캐시 + 도서관 정보나루 |
| IssuedBookReader | 만료 전 suggestion 에 이미 낸 도서 ISBN |

🚨 키 · 몸무게는 `Decimal` 이다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.rules.book_form import BookForm


class SafetyLookupError(Exception):
    """health_safety를 읽지 못함. 0행(건강정보 동의 안 함)과 다르다.

    빈 목록으로 대신 돌려주지 않는다. 교육 활동은 이때 모델을 부르지 않고 닫힌다
    (`blocked.safety`) — 루틴 · 도서 · 성장 추이는 `health_safety` 를 읽지 않아 상관없다.
    """


class UpstreamUnavailable(Exception):
    """외부 조회 실패(도서 API). 결과가 0건인 것과 다르다.

    캐시만으로 목록을 꾸미지 않고 `book_suggestion` 을 `closed.book_api` 로 닫는다.
    """


# 읽는 health_safety kind. 나머지 셋(chronic_disease · behavioral · other_medical)은 어디서도
# 읽지 않는다 — 그걸로 추천을 가르면 LLM 없는 자동 진단이다
SafetyKind = Literal["allergy", "environmental"]
# 거르는 것은 active 뿐이다. 행이 없는 것(unknown)은 DB 값이 아니다 (10/4 확정안)
SafetyStatus = Literal["active", "retracted", "none"]


@dataclass(frozen=True)
class SafetyEntry:
    kind: SafetyKind
    label: str  # 보호자가 적은 이름. 예: 라텍스 · 꽃가루
    status: SafetyStatus


@dataclass(frozen=True)
class GrowthMeasurement:
    """`child_growth_log` 한 행. 한쪽만 잰 날이 있다 — 둘 다 비면 서버가 거부한다."""

    id: UUID
    child_id: UUID
    measured_on: date
    height_cm: Decimal | None
    weight_kg: Decimal | None

    def __post_init__(self) -> None:
        if self.height_cm is None and self.weight_kg is None:
            raise ValueError("키와 몸무게가 둘 다 빈 측정은 없다")
        for value in (self.height_cm, self.weight_kg):
            if value is not None and not isinstance(value, Decimal):
                # float 로 들어오면 뺄셈에서 8.700000000000003 이 나온다
                raise TypeError(f"측정값은 Decimal 이어야 한다: {type(value).__name__}")


@dataclass(frozen=True)
class EducationObservation:
    """`observation_education` 한 행. `status='active'` 만 온다."""

    id: UUID
    child_id: UUID
    observed_on: date  # observed_range 의 시작일
    subject: str  # 병합 판정 입력 (정규화된 값)
    topic: str  # 사람이 읽는 원문
    polarity: int  # -1 / 0 / +1
    engagement_level: str | None = None  # low / mid / high


@dataclass(frozen=True)
class RoutineObservation:
    """`observation_routine` 한 행. `status='active'` 만 온다 (`stand_alone` 은 근거가 아니다)."""

    id: UUID
    child_id: UUID
    observed_on: date
    subject: str  # 행동 이름. 예: 양치하기
    routine_category: (
        str  # self_care · mealtime · household_task · social_manner · habit · transition
    )
    polarity: int
    assistance_level: str | None = (
        None  # independent · verbal_prompt · partial_assist · full_assist
    )
    completion_status: str | None = None
    trigger: str | None = None  # 습관 교정은 이 값이 있어야 시작한다


@dataclass(frozen=True)
class ActivityObservation:
    """`observation_activity` 한 행 — **참고용**. Growth 는 읽기만 하고 쓰지 않는다."""

    id: UUID
    child_id: UUID
    observed_on: date
    subject: str
    activity: str
    polarity: int


# routine 은 affinity 가 없다 (09-23) — 루틴 관찰은 티어 3 로만 인용한다
AffinityDomain = Literal["education", "activity"]

GrowthDocType = Literal[
    "learning_activity",
    "routine_step",
    "habit_strategy",
    "manner_practice",
    "rhythm_info",
    "book_guide",
    "measure_guide",
]


@dataclass(frozen=True)
class GrowthDocRow:
    """`growth_doc` 한 행. 사람이 읽고 재구성한 자료 — 참고일 뿐 개인화 근거로 세지 않는다.

    월령은 `min_month` 이상 `max_month` **미만**이다 (RAG_plan §1).
    `tags` 의 `assistance:<level>` 은 자립 단계 행이 어느 도움 수준에 해당하는지다
    (`tools/routine.py` 의 `pick_next_step`). `next_step_of` 는 사슬의 **앞 단계** 행이다.
    """

    id: UUID
    doc_key: str
    row_type: GrowthDocType
    title: str
    body: str
    min_month: int
    max_month: int
    area: str | None = None
    routine_category: str | None = None
    trigger_tags: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    setting: Literal["indoor", "outdoor", "either"] | None = None
    next_step_of: UUID | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookRow:
    """`book_catalog` 한 행. 모델이 책 제목을 지어내지 못하게 이 행의 ISBN 만 통과시킨다.

    `form` 은 `rules/book_form.py` 가 정한다. 월령 범위도 거기서 나오고, `search_books` 는 이 행의
    `age_min_month` · `age_max_month` 를 다시 믿지 않고 `form` 으로 월령을 건다.
    """

    isbn: str
    title: str
    form: BookForm
    author: str | None = None
    publisher: str | None = None
    cover_url: str | None = None
    age_min_month: int | None = None
    age_max_month: int | None = None


class ChildProfileReader(Protocol):
    async def birth_date(self, *, child_id: UUID) -> date:
        """월령은 이 값에서 코드가 계산한다 (app/rules/age.py)."""
        ...


class ConsentReader(Protocol):
    async def child_health_granted(self, *, child_id: UUID) -> bool:
        """consent(scope=child_health) 최신 행이 granted 인가."""
        ...


class SafetyReader(Protocol):
    async def growth_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        """`SafetyKind` 둘을 status 무관하게 돌려준다. 거르는 것은 호출부다.

        읽기에 실패하면 SafetyLookupError. 빈 목록을 대신 돌려주지 않는다.
        """
        ...


class GrowthLogReader(Protocol):
    async def measurements(self, *, child_id: UUID) -> list[GrowthMeasurement]:
        """측정 **전부**. 순서는 보장하지 않는다 — 시간순은 호출부가 정렬한다.

        읽기에 실패하면 예외를 올린다. 빈 목록은 "측정한 적 없음"이다.
        """
        ...


class GrowthMemoryReader(Protocol):
    async def affinities(
        self, *, child_id: UUID, domains: tuple[AffinityDomain, ...]
    ) -> list[AffinityRow]:
        """`profile_affinity`. common/evidence.py 가 순위를 매긴다."""
        ...

    async def education(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[EducationObservation]: ...

    async def routine(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[RoutineObservation]: ...

    async def activities(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]: ...


class NoticeReader(Protocol):
    async def has_notice(self, *, child_id: UUID) -> bool:
        """기관 공지가 있는가. 없으면 `lookup_notice` 만 빠진다."""
        ...


class GrowthDocReader(Protocol):
    async def search(
        self,
        *,
        months: int,
        row_type: GrowthDocType,
        routine_category: str | None,
        trigger_tags: tuple[str, ...],
        query: str,
        limit: int,
    ) -> list[GrowthDocRow]:
        """`min_month <= months < max_month` 로 거른 뒤 의미 검색 상위 `limit` 행.

        `status='approved'` 행만 돌려준다. `query` 는 코드가 조립한다 —
        보호자 발화가 들어가지 않는다.
        """
        ...

    async def chain(self, *, chain_key: str) -> list[GrowthDocRow]:
        """doc_key 가 `{chain_key}.step` 으로 시작하는 approved `routine_step` 행 전부.

        월령으로 거르지 않는다 — 사슬 중간이 빠지면 `pick_next_step` 이 다음 칸을 못 고른다.
        순서는 보장하지 않는다(`ordered_chain` 이 세운다). 사슬 이름은 `doc_seed.chain_key` 다.
        """
        ...


class BookSource(Protocol):
    async def search(self, *, keywords: tuple[str, ...], limit: int) -> list[BookRow]:
        """캐시를 먼저 보고 API 를 부른다. 실패하면 UpstreamUnavailable — 빈 목록이 아니다."""
        ...


class IssuedBookReader(Protocol):
    async def isbns(self, *, child_id: UUID, now: datetime) -> frozenset[str]:
        """만료 전 `suggestion` 에 이미 있는 도서 ISBN. 승인 · 거절한 책도 포함한다."""
        ...


@dataclass(frozen=True)
class GrowthPorts:
    """아이 하나 몫의 읽기 포트 묶음. `notice` · `books` · `issued_books` 는 없을 수 있다.

    `books` 가 없으면 도서 라벨이 닫힌다. `issued_books` 가 없으면 `propose_books` 가 `RuntimeError`
    를 올린다 — 낸 책을 모르는 채 통과시키면 "다른 책도" 에 같은 책이 다시 나온다.
    """

    profile: ChildProfileReader
    consent: ConsentReader
    safety: SafetyReader
    growth_log: GrowthLogReader
    memory: GrowthMemoryReader
    docs: GrowthDocReader
    notice: NoticeReader | None = None  # None = 공지 표 없음 → lookup_notice 닫힘
    books: BookSource | None = None  # None = 인증키 없음 → book_suggestion 닫힘
    issued_books: IssuedBookReader | None = None
