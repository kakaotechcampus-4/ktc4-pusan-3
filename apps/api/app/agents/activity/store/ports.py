"""Activity가 읽는 데이터의 계약.

구현체는 DB·외부 API가 연결된 뒤에 붙인다. 지금은 포트와 테스트용 InMemory 구현까지다.
Activity는 쓰기 포트가 없다 — 기록은 Memory가, 추천 저장은 주입된 writer가, 일정 초안은 서버가
만든다 (D11). 외부 API 어댑터는 `app/integrations/`에 두고 `app/api`가 주입한다
(`app/agents/`는 `integrations`를 import할 수 없다 — D8).

🚨 **원좌표는 여기까지 오지 않는다.** 좌표는 요청 바디로만 받아 받는 즉시 5km 격자로 뭉갠다.
포트는 `WeatherGrid`만 받는다 — 원좌표가 DB·로그·모델 입력·예외 메시지 어디에도 없게 한다 (4-3).

| 포트 | 연결 대상 |
| ChildProfileReader | Child_Profile — birth_date 만 |
| ConsentReader | consent(scope=child_health) 최신 행 |
| SafetyReader | health_safety — allergy · dietary_restriction · environmental (D7) |
| ActivityMemoryReader | profile_affinity(domain=activity) · observation_activity |
| ScheduleReader | 아이 일정 — 읽기 전용 |
| ActivityDocReader | activity_doc — 월령 슬라이스 + 의미 검색 |
| WeatherSource | 기상청 단기예보 · 에어코리아 · 생활기상지수 · 기상특보 |
| PlaceSource | Kakao Local · 도시공원 표준데이터 적재분 |
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol
from uuid import UUID

from app.agents.activity.schemas.common import PlaceCategory
from app.agents.common.evidence import AffinityRow
from app.agents.common.gate import SafetyState


class SafetyLookupError(Exception):
    """health_safety를 읽지 못함. 0행(건강정보 동의 안 함)과 다르다.

    빈 목록으로 대신 돌려주지 않는다. Activity가 이때 어떻게 할지는 D7 을 따른다.
    """


class UpstreamUnavailable(Exception):
    """외부 조회 실패. 결과가 0건인 것과 다르다.

    기본값으로 메우지 않는다 — 날씨 실패는 "맑음"이 아니고 미세먼지 실패는 "좋음"이 아니다 (D8).
    메시지에 격자·장소명을 넣지 않는다.
    """


# 읽는 health_safety kind. 나머지 셋(chronic_disease · behavioral · other_medical)은 읽지 않는다 —
# "천식이면 야외 금지" 같은 표가 곧 LLM 없는 자동 진단이다 (D7)
SafetyKind = Literal["allergy", "dietary_restriction", "environmental"]


@dataclass(frozen=True)
class SafetyEntry:
    kind: SafetyKind
    label: str  # 예: 밀
    state: SafetyState  # 거르는 것은 active 뿐이다
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActivityObservation:
    """`observation_activity` 한 행. `status='active'`만 온다."""

    id: UUID
    child_id: UUID
    observed_on: date  # observed_range 의 시작일
    subject: str  # 병합 판정 입력 (정규화된 값)
    activity: str  # 사람이 읽는 원문. 예: 레고 조립
    polarity: int  # -1 / 0 / +1
    updated_at: datetime  # suggestion_evidence.source_updated_at 에 들어간다


@dataclass(frozen=True)
class AffinityRecord:
    """`profile_affinity`(domain=activity) 한 행.

    공통 `AffinityRow`에는 `updated_at`이 없다. 인용하려면 `source_updated_at`이 필요해서
    여기서 같이 싣는다.
    """

    row: AffinityRow
    updated_at: datetime


@dataclass(frozen=True)
class ScheduleBlock:
    """아이 일정 한 칸. 제목은 싣지 않는다 — 비는 시간과 겹침만 본다."""

    starts_at: datetime
    ends_at: datetime | None
    all_day: bool = False


@dataclass(frozen=True)
class ActivityDocRow:
    """`activity_doc` 한 행. 사람이 읽고 재구성한 놀이 자료 (D9)."""

    id: UUID
    doc_key: str
    body: str
    min_month: int
    max_month: int | None
    written_at: datetime  # suggestion_evidence.source_updated_at 에 들어간다


@dataclass(frozen=True)
class WeatherGrid:
    """기상청 5km 격자. 원좌표는 이 값으로 뭉갠 뒤 버린다."""

    nx: int
    ny: int


@dataclass(frozen=True)
class Forecast:
    sky: str | None  # 기상청 하늘상태 라벨 그대로. None = 확인 못 함
    precip_mm_per_h: float | None  # 파싱 실패도 None 이다 — "강수없음"과 다르다
    pop_percent: int | None  # 강수확률


@dataclass(frozen=True)
class AirQuality:
    pm10: int | None  # ㎍/㎥
    pm25: int | None
    ozone_ppm: float | None


AdvisoryLevel = Literal["none", "advisory", "warning"]


@dataclass(frozen=True)
class Advisories:
    """기상특보 발효 여부. 기준 수치는 받지 않는다 — 개정되면 자동으로 따라가게 (4-2)."""

    heat: AdvisoryLevel
    cold: AdvisoryLevel
    severe: bool  # 강풍 · 호우 · 대설 · 태풍 중 하나라도 발효 중


@dataclass(frozen=True)
class PlaceRow:
    """장소 한 곳. 화이트리스트 필드만 — 전화번호 · 관리기관 · 가격 · 평점은 싣지 않는다."""

    name: str
    category: PlaceCategory
    distance_m: int
    source: Literal["kakao_local", "city_park"]


class ChildProfileReader(Protocol):
    async def birth_date(self, *, child_id: UUID) -> date:
        """월령은 이 값에서 코드가 계산한다 (app/rules/age.py)."""
        ...


class ConsentReader(Protocol):
    async def child_health_granted(self, *, child_id: UUID) -> bool:
        """consent(scope=child_health) 최신 행이 granted 인가."""
        ...


class SafetyReader(Protocol):
    async def activity_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        """SafetyKind 세 가지를 state 무관하게 돌려준다. 거르는 것은 호출부다.

        읽기에 실패하면 SafetyLookupError. 빈 목록을 대신 돌려주지 않는다.
        """
        ...


class ActivityMemoryReader(Protocol):
    async def affinities(self, *, child_id: UUID) -> list[AffinityRecord]:
        """`profile_affinity`(domain=activity). common/evidence.py 가 순위를 매긴다."""
        ...

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ActivityObservation]:
        """`observation_activity` 구간 조회. `affinity_id=NULL` 인 관찰도 온다 (K-7)."""
        ...


class ScheduleReader(Protocol):
    async def blocks(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[ScheduleBlock]: ...


class ActivityDocReader(Protocol):
    async def search(self, *, months: int, query: str, limit: int) -> list[ActivityDocRow]:
        """`min_month <= months <= max_month` 로 거른 뒤 의미 검색 상위 `limit` 행."""
        ...


class WeatherSource(Protocol):
    """네 조회는 따로 실패한다. 하나가 죽어도 나머지는 온다 — 미세먼지만 실패하면 야외는 허용하되
    고지한다 (D8). 실패는 각자 UpstreamUnavailable.
    """

    async def forecast(self, *, grid: WeatherGrid, day: date) -> Forecast: ...

    async def air_quality(self, *, grid: WeatherGrid) -> AirQuality: ...

    async def uv_grade(self, *, grid: WeatherGrid, day: date) -> str | None:
        """API 가 주는 자외선 등급 문자열 그대로. 우리가 다시 분류하지 않는다."""
        ...

    async def advisories(self, *, grid: WeatherGrid) -> Advisories: ...


class PlaceSource(Protocol):
    async def nearby(
        self, *, grid: WeatherGrid, category: PlaceCategory, radius_m: int
    ) -> list[PlaceRow]:
        """🚨 검색어는 category 하나다. 모델이 만든 문자열을 받지 않는다 (D8)."""
        ...


@dataclass(frozen=True)
class ActivityPorts:
    """Activity Agent 가 요청 하나를 처리하는 동안 쥐는 포트 묶음."""

    profile: ChildProfileReader
    consent: ConsentReader
    safety: SafetyReader
    memory: ActivityMemoryReader
    schedule: ScheduleReader
    docs: ActivityDocReader
    weather: WeatherSource | None = None  # None 이면 조회 실패와 같다 — 실내만
    places: PlaceSource | None = None  # None 이면 장소가 필요 없는 활동만
