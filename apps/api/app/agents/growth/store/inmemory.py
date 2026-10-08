"""Growth 포트의 인메모리 구현. 테스트·eval이 DB·외부 API 없이 GrowthPorts를 주입할 때 쓴다.

아이 기록 포트는 행이 `child_id`로 나뉘도록 딕셔너리로 둔다. 실패를 흉내 낼 때는 `fail=True` —
빈 결과와 구분해야 한다 (루트 §2).

🚨 **동의는 기본값이 없다.** Food 의 `InMemoryConsent(..., default=True)` 를 복사하면 동의를
철회한 아이의 키 · 몸무게가 테스트에서 아무렇지 않게 읽힌다. 어느 아이가 동의했는지 생성자에서
명시해야 하고, 적지 않은 아이는 `KeyError` 다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.agents.growth.store.ports import (
    ActivityObservation,
    AffinityDomain,
    BookRow,
    EducationObservation,
    GrowthDocRow,
    GrowthDocType,
    GrowthMeasurement,
    GrowthPorts,
    NoticeReader,
    RoutineObservation,
    SafetyEntry,
    SafetyLookupError,
    UpstreamUnavailable,
)


class InMemoryProfile:
    def __init__(self, birth_dates: dict[UUID, date] | None = None) -> None:
        self._birth_dates = dict(birth_dates or {})

    async def birth_date(self, *, child_id: UUID) -> date:
        return self._birth_dates[child_id]


class InMemoryConsent:
    def __init__(self, granted: dict[UUID, bool]) -> None:
        self._granted = dict(granted)

    async def child_health_granted(self, *, child_id: UUID) -> bool:
        if child_id not in self._granted:
            raise KeyError(f"동의 여부를 지정하지 않은 아이: {child_id}")
        return self._granted[child_id]


class InMemorySafety:
    def __init__(self, entries: Sequence[SafetyEntry] = (), *, fail: bool = False) -> None:
        self._entries = list(entries)
        self._fail = fail
        self.calls = 0  # 동의가 없거나 교육이 아니면 아예 읽지 않는지 테스트가 본다

    async def growth_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        self.calls += 1
        if self._fail:
            raise SafetyLookupError("health_safety 조회 실패 (테스트 주입)")
        return list(self._entries)


class InMemoryGrowthLog:
    def __init__(self, rows: Sequence[GrowthMeasurement] = ()) -> None:
        self._rows = list(rows)
        self.calls = 0  # 동의가 없으면 아예 읽지 않는지 테스트가 본다

    async def measurements(self, *, child_id: UUID) -> list[GrowthMeasurement]:
        self.calls += 1
        return [row for row in self._rows if row.child_id == child_id]


class InMemoryGrowthMemory:
    def __init__(
        self,
        *,
        affinities: Sequence[tuple[AffinityDomain, AffinityRow]] = (),
        education: Sequence[EducationObservation] = (),
        routine: Sequence[RoutineObservation] = (),
        activities: Sequence[ActivityObservation] = (),
    ) -> None:
        self._affinities = list(affinities)
        self._education = list(education)
        self._routine = list(routine)
        self._activities = list(activities)

    async def affinities(
        self, *, child_id: UUID, domains: tuple[AffinityDomain, ...]
    ) -> list[AffinityRow]:
        return [row for domain, row in self._affinities if domain in domains]

    async def education(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[EducationObservation]:
        return [
            r
            for r in self._education
            if r.child_id == child_id and date_from <= r.observed_on <= date_to
        ]

    async def routine(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[RoutineObservation]:
        return [
            r
            for r in self._routine
            if r.child_id == child_id and date_from <= r.observed_on <= date_to
        ]

    async def activities(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]:
        return [
            r
            for r in self._activities
            if r.child_id == child_id and date_from <= r.observed_on <= date_to
        ]


class InMemoryNotice:
    def __init__(self, *, has_notice: bool) -> None:
        self._has_notice = has_notice

    async def has_notice(self, *, child_id: UUID) -> bool:
        return self._has_notice


class InMemoryGrowthDocs:
    """의미 검색은 흉내 내지 않는다. 월령 슬라이스 · 종류 · 카테고리만 거르고 넣은 순서대로 준다."""

    def __init__(self, rows: Sequence[GrowthDocRow] = ()) -> None:
        self._rows = list(rows)

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
        rows = [
            r
            for r in self._rows
            if r.row_type == row_type
            and r.min_month <= months < r.max_month
            and (routine_category is None or r.routine_category == routine_category)
            and (not trigger_tags or set(trigger_tags) & set(r.trigger_tags))
        ]
        return rows[:limit]


class InMemoryBooks:
    def __init__(self, rows: Sequence[BookRow] = (), *, fail: bool = False) -> None:
        self._rows = list(rows)
        self._fail = fail

    async def search(self, *, keywords: tuple[str, ...], limit: int) -> list[BookRow]:
        if self._fail:
            raise UpstreamUnavailable("도서 조회 실패 (테스트 주입)")
        return self._rows[:limit]


class InMemoryIssuedBooks:
    def __init__(self, isbns: Sequence[str] = ()) -> None:
        self._isbns = frozenset(isbns)

    async def isbns(self, *, child_id: UUID, now: datetime) -> frozenset[str]:
        return self._isbns


def in_memory_ports(
    child_id: UUID,
    birth_date: date,
    *,
    consent: bool,
    safety: InMemorySafety | None = None,
    growth_log: InMemoryGrowthLog | None = None,
    memory: InMemoryGrowthMemory | None = None,
    docs: InMemoryGrowthDocs | None = None,
    notice: NoticeReader | None = None,
    books: InMemoryBooks | None = None,
    issued_books: InMemoryIssuedBooks | None = None,
) -> GrowthPorts:
    """아이 하나 몫의 포트 묶음. `consent` 는 꼭 적는다 — 기본값이 없다.

    안 넘긴 기록 포트는 빈 데이터, `notice` · `books` 는 None(표 없음 · 키 없음)이다.
    """
    return GrowthPorts(
        profile=InMemoryProfile({child_id: birth_date}),
        consent=InMemoryConsent({child_id: consent}),
        safety=safety or InMemorySafety(),
        growth_log=growth_log or InMemoryGrowthLog(),
        memory=memory or InMemoryGrowthMemory(),
        docs=docs or InMemoryGrowthDocs(),
        notice=notice,
        books=books,
        issued_books=issued_books,
    )
