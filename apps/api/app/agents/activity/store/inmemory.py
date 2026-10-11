"""Activity 포트의 인메모리 구현. 테스트·eval이 DB·외부 API 없이 ActivityPorts를 주입할 때 쓴다.

아이 기록 포트(memory · schedule)는 행이 `child_id`로 나뉘도록 딕셔너리로 둔다.
`child_id`가 없는 공용 데이터 포트(docs · weather · places)는 한 인스턴스가 한 시나리오 몫의
데이터만 쥔다고 본다. 실패를 흉내 낼 때는 `fail=True` — 빈 결과와 구분해야 한다 (루트 §2).
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from app.agents.activity.doc_seed import activity_doc_seed
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.store.ports import (
    ActivityDocRow,
    ActivityObservation,
    ActivityPorts,
    CoarseLocation,
    PlaceRow,
    RawAdvisories,
    RawAir,
    RawForecast,
    SafetyEntry,
    SafetyLookupError,
    ScheduleBlock,
    UpstreamUnavailable,
    WeatherGrid,
)
from app.agents.common.evidence import AffinityRow


class InMemoryProfile:
    def __init__(self, birth_dates: dict[UUID, date] | None = None) -> None:
        self._birth_dates = dict(birth_dates or {})

    async def birth_date(self, *, child_id: UUID) -> date:
        return self._birth_dates[child_id]


class InMemoryConsent:
    def __init__(self, granted: dict[UUID, bool] | None = None, *, default: bool = True) -> None:
        self._granted = dict(granted or {})
        self._default = default

    async def child_health_granted(self, *, child_id: UUID) -> bool:
        return self._granted.get(child_id, self._default)


class InMemorySafety:
    def __init__(self, entries: Sequence[SafetyEntry] = (), *, fail: bool = False) -> None:
        self._entries = list(entries)
        self._fail = fail
        self.calls = 0  # 동의가 없으면 아예 읽지 않는지 테스트가 본다

    async def activity_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        self.calls += 1
        if self._fail:
            raise SafetyLookupError("health_safety 조회 실패 (테스트 주입)")
        return list(self._entries)


class InMemoryActivityMemory:
    def __init__(
        self,
        affinities: dict[UUID, Sequence[AffinityRow]] | None = None,
        observations: dict[UUID, Sequence[ActivityObservation]] | None = None,
    ) -> None:
        self._affinities = {k: list(v) for k, v in (affinities or {}).items()}
        self._observations = {k: list(v) for k, v in (observations or {}).items()}

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return list(self._affinities.get(child_id, ()))

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]:
        return [
            row
            for row in self._observations.get(child_id, ())
            if date_from <= row.observed_on <= date_to
        ]


class InMemorySchedule:
    def __init__(self, blocks: dict[UUID, Sequence[ScheduleBlock]] | None = None) -> None:
        self._blocks = {k: list(v) for k, v in (blocks or {}).items()}

    async def blocks(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ScheduleBlock]:
        return [
            block
            for block in self._blocks.get(child_id, ())
            if date_from <= block.starts_at.date() <= date_to
        ]


class InMemoryActivityDocs:
    """의미 검색은 흉내 내지 않는다. 월령 슬라이스만 거르고 넣은 순서대로 돌려준다."""

    def __init__(self, rows: Sequence[ActivityDocRow] = ()) -> None:
        self._rows = list(rows)

    @classmethod
    def from_seed(cls) -> "InMemoryActivityDocs":
        """`reference/activity_doc.yaml` 의 검수를 마친(`approved`) 행. DB 전 eval · 개발용."""
        return cls(
            entry.to_row() for entry in activity_doc_seed() if entry.meta.status == "approved"
        )

    async def search(self, *, months: int, query: str, limit: int) -> list[ActivityDocRow]:
        sliced = [row for row in self._rows if row.min_month <= months < row.max_month]
        return sliced[:limit]


class InMemoryWeather:
    """조회마다 따로 실패시킬 수 있다 — `fail={"air_quality"}` 면 미세먼지만 죽는다."""

    def __init__(
        self,
        *,
        forecast: RawForecast | None = None,
        air: RawAir | None = None,
        uv: str | None = None,
        advisories: RawAdvisories | None = None,
        fail: frozenset[str] = frozenset(),
    ) -> None:
        self._forecast = forecast or RawForecast(sky="1", pcp="강수없음", pop="0")
        self._air = air or RawAir(
            pm10="30", pm10_flag=None, pm25="15", pm25_flag=None, o3="0.03", o3_flag=None
        )
        self._uv = uv
        self._advisories = advisories or RawAdvisories(result_code="03")  # 특보 없음
        self._fail = fail

    def _check(self, name: str) -> None:
        if name in self._fail:
            raise UpstreamUnavailable(f"{name} 조회 실패 (테스트 주입)")

    async def forecast(self, *, grid: WeatherGrid, day: date) -> RawForecast:
        self._check("forecast")
        return self._forecast

    async def air_quality(self, *, grid: WeatherGrid) -> RawAir:
        self._check("air_quality")
        return self._air

    async def uv(self, *, grid: WeatherGrid, day: date) -> str | None:
        self._check("uv")
        return self._uv

    async def advisories(self, *, grid: WeatherGrid) -> RawAdvisories:
        self._check("advisories")
        return self._advisories


class InMemoryPlaces:
    def __init__(self, rows: Sequence[PlaceRow] = (), *, fail: bool = False) -> None:
        self._rows = list(rows)
        self._fail = fail

    async def nearby(
        self, *, location: CoarseLocation, category: PlaceCategory, radius_m: int
    ) -> list[PlaceRow]:
        if self._fail:
            raise UpstreamUnavailable("장소 조회 실패 (테스트 주입)")
        return [
            row for row in self._rows if row.category == category and row.distance_m <= radius_m
        ]


def in_memory_ports(
    child_id: UUID,
    birth_date: date,
    *,
    consent: bool = True,
    safety: InMemorySafety | None = None,
    memory: InMemoryActivityMemory | None = None,
    schedule: InMemorySchedule | None = None,
    docs: InMemoryActivityDocs | None = None,
    weather: InMemoryWeather | None = None,
    places: InMemoryPlaces | None = None,
) -> ActivityPorts:
    """아이 하나 몫의 포트 묶음. 안 넘긴 포트는 빈 데이터, weather · places 는 None(키 없음)이다."""
    return ActivityPorts(
        profile=InMemoryProfile({child_id: birth_date}),
        consent=InMemoryConsent({child_id: consent}),
        safety=safety or InMemorySafety(),
        memory=memory or InMemoryActivityMemory(),
        schedule=schedule or InMemorySchedule(),
        docs=docs or InMemoryActivityDocs(),
        weather=weather,
        places=places,
    )
