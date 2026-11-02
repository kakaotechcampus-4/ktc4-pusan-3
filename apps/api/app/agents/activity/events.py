"""행사(TourAPI 축제공연행사) 원문을 읽고 거르는 규칙. 어댑터는 원문만 넘긴다 (`core/event_raw.py`).

`weather.py` 와 같은 자리다 — 해석을 어댑터에 두면 같은 규칙을 테스트 밖에서 한 번 더 짠다.

- 못 읽는 행(이름 · 날짜 · 좌표 · 종류가 비거나 깨짐)은 뺀다. 기본값으로 메우지 않는다.
- 온라인 행사는 갈 곳이 없어 뺀다.
- 🚨 술 · 주류 행사는 이름으로 뺀다. 아이와 할 활동으로 내는 곳이 아니다 — 보호자가 즐기는 행사라도
  이 서비스가 아이 나들이로 권하지 않는다 (11-02 결정).
- 🚨 관람 연령이 아이보다 높으면 뺀다. 보호자와 함께 들어갈 수 있다고 적혀 있으면 둔다 — 이
  서비스의 나들이는 보호자와 간다. 읽을 수 없는 조건은 뺀다(더 막는 쪽).
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from app.agents.activity.schemas.common import EventKind
from app.agents.activity.store.ports import CoarseLocation, EventRow
from app.core.event_raw import RawEvent

EARTH_RADIUS_M = 6_371_000

# 시도(`areas.PROVINCE_ALIASES` 의 이름) → TourAPI 법정동 시도 코드(lDongRegnCd). 11-02 실측 —
# 세종은 다섯 자리로 오고, 전남광주는 통합 코드다. 옛 코드(광주 29 · 전남 46 · 강원 42 · 전북 45)도
# 같은 시도로 본다 — 표가 늦게 바뀐 행이 섞일 수 있다
TOUR_REGION_CODES: dict[str, tuple[str, ...]] = {
    "서울특별시": ("11",),
    "부산광역시": ("26",),
    "대구광역시": ("27",),
    "인천광역시": ("28",),
    "대전광역시": ("30",),
    "울산광역시": ("31",),
    "세종특별자치시": ("36110", "36"),
    "경기도": ("41",),
    "강원특별자치도": ("51", "42"),
    "충청북도": ("43",),
    "충청남도": ("44",),
    "전북특별자치도": ("52", "45"),
    "전남광주통합특별시": ("12", "29", "46"),
    "경상북도": ("47",),
    "경상남도": ("48",),
    "제주특별자치도": ("50",),
}

_KINDS = {"EV01": EventKind.FESTIVAL, "EV02": EventKind.PERFORMANCE, "EV03": EventKind.EVENT}

# 행사 이름 앞의 연도 · 회차와 괄호 · 부제. 모델은 긴 공식 이름을 문장에 쓸 때 이 부분을 뗀다
# (11-03 실측 — "2026 제5회 ○○비엔날레 … : 부제" 를 "○○비엔날레" 로 썼다)
_LEADING_YEAR = re.compile(r"^(?:19|20)\d{2}\s*년?\s+")
_LEADING_ROUND = re.compile(r"^제?\s*\d+\s*회\s+")
_BRACKETED = re.compile(r"[(\[<〈《「『【][^)\]>〉》」』】]*[)\]>〉》」』】]")
_SUBTITLE = re.compile(r"\s*(?:[:：]|\s[-–—]\s).*$")

# 술 · 주류 행사 이름. "술" 한 글자로 걸면 예술 · 미술 · 기술 · 마술이 걸려서 "술" 은 낱말 첫 글자일
# 때만 본다(앞이 한글이 아님). 술래잡기는 빼 둔다
_LIQUOR_WORDS = (
    "맥주",
    "비어",
    "와인",
    "와이너리",
    "포도주",
    "주류",
    "막걸리",
    "소주",
    "전통주",
    "우리술",
    "칵테일",
    "위스키",
    "하이볼",
    "양조",
)
_LIQUOR = re.compile(r"(?:^|[^가-힣])술(?!래)")

# 보호자와 함께면 들어갈 수 있다는 문구. "동반 불가" 는 아니다
# 보호자 동반을 허용으로 적은 문구만 — "동행 필수" · "동반 시 입장 가능" · "동반 하에 관람가".
# "동반 시에도 관람 불가" 처럼 동반이 나오기만 하는 문구로 들이지 않는다 (#668 리뷰)
_WITH_GUARDIAN = re.compile(
    r"(?:보호자|성인|부모)\s*(?:동반|동행)\s*(?:시|하에)?\s*"
    r"(?:필수|가능|(?:입장|관람|참여|참가|이용)\s*가능?)"
)
# 이 말이 같이 있으면 동반 문구를 믿지 않고 연령으로 본다 — 어느 아이를 막는지 못 읽으면 뺀다
_REFUSED = ("불가", "금지")
_MIN_MONTHS = re.compile(r"(\d+)\s*개월\s*이상")
# "만 13세 이상" · "만 13세(중학생) 이상"
_MIN_YEARS = re.compile(r"(\d+)\s*세\s*(?:\([^)]*\)\s*)?이상")
_ALL_AGES = ("전 연령", "전연령", "누구나", "제한 없음", "제한없음", "전체 관람", "전체관람")


@dataclass(frozen=True)
class Event:
    content_id: str
    name: str
    kind: EventKind
    starts_on: date
    ends_on: date
    # 행사 위치(공공데이터). 보호자 위치가 아니다 — 거리 계산에만 쓰고 모델에 주지 않는다
    lat: float
    lon: float
    region_code: str = ""  # 법정동 시도 코드 — 시 · 도 행사 검색(`in_province`)


def read_event(raw: RawEvent) -> Event | None:
    """원문 한 행을 읽는다. 못 읽는 행 · 온라인 · 술 행사는 None 이다."""
    name = raw.title.strip()
    kind = _KINDS.get(raw.kind_code.strip())
    starts_on = _day(raw.start)
    ends_on = _day(raw.end)
    lat = _number(raw.mapy)
    lon = _number(raw.mapx)
    if not name or kind is None or starts_on is None or ends_on is None or starts_on > ends_on:
        return None
    if lat is None or lon is None or not (-90 < lat < 90 and -180 < lon < 180) or lat == lon == 0:
        return None
    if raw.progress.strip() == "온라인" or _liquor(name):
        return None
    return Event(
        content_id=raw.content_id,
        name=name,
        kind=kind,
        starts_on=starts_on,
        ends_on=ends_on,
        lat=lat,
        lon=lon,
        region_code=raw.region_code.strip(),
    )


def core_name(name: str) -> str:
    """행사 이름에서 앞의 연도 · 회차와 괄호 · 부제를 뗀 이름. 다 떼고 남는 게 없으면 원래 이름이다.

    출력 검증이 place_name · 문장에서 행사를 알아보는 데 쓴다(`review.py`). 화면에는 원래 이름이
    나간다.
    """
    core = _LEADING_ROUND.sub("", _LEADING_YEAR.sub("", name.strip()))
    core = " ".join(_SUBTITLE.sub("", _BRACKETED.sub("", core)).split())
    return core or name.strip()


def open_on(event: Event | EventRow, days: Iterable[date]) -> bool:
    """그날들 중 하루라도 기간 안인가. 시작일 · 종료일을 포함한다."""
    return any(event.starts_on <= day <= event.ends_on for day in days)


def in_province(event: Event, province: str) -> bool:
    """그 시도의 행사인가 — TourAPI 법정동 시도 코드로 본다. 코드가 비면 어느 시도도 아니다."""
    return event.region_code in TOUR_REGION_CODES.get(province, ())


def age_allows(age_limit: str, months: int) -> bool:
    """관람 연령 원문이 이 월령의 아이를 들이는가.

    보호자 동반을 허용으로 적었으면 된다 — 불가 · 금지가 같이 적혀 있으면 이 길로 들이지 않는다.
    최소 연령(개월 · 세)을 읽으면 그 값과 비교한다 — "전 연령" 보다 먼저 본다(더 막는 쪽).
    비어 있거나 제한이 없다는 말이면 된다. 그 밖의 문구는 안 된다.
    """
    text = age_limit.strip()
    if not text:
        return True
    if _WITH_GUARDIAN.search(text) and not any(word in text for word in _REFUSED):
        return True
    if found := _MIN_MONTHS.search(text):
        return months >= int(found[1])
    if found := _MIN_YEARS.search(text):
        return months >= int(found[1]) * 12
    return any(word in text for word in _ALL_AGES)


def distance_m(event: Event, origin: CoarseLocation) -> int:
    """행사와 기준점 사이의 거리(m, haversine). 서버 안에서만 쓰고 모델에 주지 않는다."""
    lat1, lat2 = math.radians(origin.lat), math.radians(event.lat)
    dlat = lat2 - lat1
    dlon = math.radians(event.lon - origin.lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)))


def _liquor(name: str) -> bool:
    return any(word in name for word in _LIQUOR_WORDS) or _LIQUOR.search(name) is not None


def _day(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
