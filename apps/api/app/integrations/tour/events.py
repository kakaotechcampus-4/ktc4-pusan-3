"""TourAPI 축제공연행사(`searchFestival2`) · 소개정보(`detailIntro2`) · 개요(`detailCommon2`) —
Activity `EventSource` 구현.

하는 일 (Activity D8 · D9 행사)
  1. 그날 이후에 끝나는 전국 행사(진행 중 포함)를 받는다. 위치를 보내지 않는다 — 행사 쪽으로는
     위치 파생값이 하나도 나가지 않는다. 위치기반 조회(`locationBasedList2`)는 좌표를 보내므로
     쓰지 않는다. 받은 목록은 모든 사용자가 같이 쓰므로 그날 하루 프로세스에 하나만 둔다
     (`--workers 1` 전제, #134)
  2. 소개정보 · 개요는 Activity 가 가까운 행사만 묻는다. 행사마다 그날 하루 둔다 — 개요는 일정을
     개요에만 적은 행사가 있어서 묻는다(Activity `event_schedule.py`)

원문 문자열을 `core/event_raw.py` 모양에 옮겨 담기만 한다 — 모양에 없는 칸(요금 · 전화번호 ·
홈페이지 · 이미지)은 밖으로 나가지 않는다. 거리 · 거르기 · 관람 연령 읽기는 Activity `events.py` 다.
인증키는 날씨와 같은 공공데이터포털 키다(`DATA_GO_KR_SERVICE_KEY` — TourAPI 활용신청이 된 키).
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.culture_raw import RawSite
from app.core.event_raw import RawEvent, RawEventIntro
from app.core.weather_raw import UpstreamUnavailable
from app.integrations.weather.public_data import get_json, result_code

log = logging.getLogger(__name__)

BASE_URL = "https://apis.data.go.kr/B551011/KorService2"
KST = ZoneInfo("Asia/Seoul")

# 한 쪽에 다 받는다. 11-02 실측: 오늘 이후에 끝나는 행사 102건(0.4s) · 8월 초 기준 526건.
# 넘치면 다음 쪽을 부르되 `MAX_PAGES` 에서 자른다 — 한 run 의 시간 예산을 넘기지 않게 (잠정)
ROWS = 1000
MAX_PAGES = 3
# 축제공연행사의 콘텐츠 타입. 소개정보는 타입마다 칸이 달라 같이 보낸다
EVENT_CONTENT_TYPE = 15
# 박물관 · 과학관 · 체험관을 고르는 타입 — 문화시설(박물관 · 미술관 · 과학관) · 관광지(체험관이 여기
# 섞인다). 11-09 실측 부산 125 · 353행, 경남 176 · 1380행
SITE_CONTENT_TYPES = ("14", "12")

_OK = "0000"
_COMMON = {"MobileOS": "ETC", "MobileApp": "icatch", "_type": "json"}


class TourEvents:
    """행사 어댑터. 프로세스에 하나를 둔다 — `app/main.py` 가 만들고 닫는다."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(transport=transport)
        self._now = now or (lambda: datetime.now(KST))
        self._events: tuple[date, tuple[RawEvent, ...]] | None = None
        self._intros: dict[str, RawEventIntro] = {}
        self._overviews: dict[str, str] = {}
        self._detail_day: date | None = None
        self._lock = asyncio.Lock()
        self._province_codes: dict[str, str] | None = None
        self._sites: dict[str, tuple[date, tuple[RawSite, ...]]] = {}
        # 시 · 도마다 따로 잠근다 — 한 시 · 도를 처음 채우는 동안 다른 시 · 도는 기다리지 않는다
        # (#858 리뷰)
        self._site_locks: dict[str, asyncio.Lock] = {}
        self._codes_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def events(self, *, ending_on_or_after: date) -> list[RawEvent]:
        """그날 이후에 끝나는 전국 행사. 같은 날은 한 번만 부른다. 실패는 캐시하지 않는다."""
        async with self._lock:
            if self._events is None or self._events[0] != ending_on_or_after:
                self._events = (ending_on_or_after, await self._fetch(ending_on_or_after))
            return list(self._events[1])

    async def intro(self, *, content_id: str) -> RawEventIntro:
        """행사 하나의 관람 연령 · 장소 · 일정 칸. 없는 행사는 UpstreamUnavailable."""
        self._roll_day()
        if (cached := self._intros.get(content_id)) is not None:
            return cached
        payload = await get_json(
            self._client,
            f"{BASE_URL}/detailIntro2",
            {**_COMMON, "contentId": content_id, "contentTypeId": EVENT_CONTENT_TYPE},
            what="행사 소개정보",
        )
        items = _items(payload, what="행사 소개정보")
        if not items:
            raise UpstreamUnavailable("행사 소개정보가 없다")
        item = items[0]
        intro = RawEventIntro(
            age_limit=_text(item.get("agelimit")),
            place=_text(item.get("eventplace")),
            playtime=_text(item.get("playtime")),
            program=_text(item.get("program")),
        )
        self._intros[content_id] = intro
        return intro

    async def overview(self, *, content_id: str) -> str:
        """행사 하나의 개요 원문. 없는 행사는 UpstreamUnavailable."""
        self._roll_day()
        if (cached := self._overviews.get(content_id)) is not None:
            return cached
        payload = await get_json(
            self._client,
            f"{BASE_URL}/detailCommon2",
            {**_COMMON, "contentId": content_id},
            what="행사 개요",
        )
        items = _items(payload, what="행사 개요")
        if not items:
            raise UpstreamUnavailable("행사 개요가 없다")
        overview = _text(items[0].get("overview"))
        self._overviews[content_id] = overview
        return overview

    async def sites(self, *, province: str) -> list[RawSite]:
        """그 시 · 도의 문화시설 · 관광지(박물관 · 과학관 · 체험관을 고르는 목록).

        좌표를 보내지 않는다 — 시 · 도 이름만 법정동 시도 코드(`ldongCode2`)로 바꿔 지역기반 목록을
        받는다. 위치기반 목록(`locationBasedList2`)은 예전 지역 코드가 붙은 행만 돌려줘서(부산
        문화시설 125행 중 5행, 11-09 실측) 쓰지 않는다. 시 · 도마다 그날 하루 둔다. 모르는 시 · 도와
        조회 실패는 UpstreamUnavailable 이다 — 실패는 캐시하지 않는다.
        """
        code = (await self._codes()).get(province)
        if code is None:
            raise UpstreamUnavailable("모르는 시 · 도다")
        async with self._site_locks.setdefault(code, asyncio.Lock()):
            today = self._now().astimezone(KST).date()
            cached = self._sites.get(code)
            if cached is None or cached[0] != today:
                # 두 타입은 동시에 받는다 — 처음 오는 사용자의 run 20초 안에 들어가는 시간이다
                lists = await asyncio.gather(
                    *(self._fetch_sites(code, content_type) for content_type in SITE_CONTENT_TYPES)
                )
                cached = (today, tuple(row for rows in lists for row in rows))
                self._sites[code] = cached
            return list(cached[1])

    async def _codes(self) -> dict[str, str]:
        """시 · 도 이름 → 법정동 시도 코드. 프로세스에 한 번 받는다(통합 시 · 도 이름이 바뀌어도
        재시작으로 따라간다)."""
        async with self._codes_lock:
            if self._province_codes is None:
                payload = await get_json(
                    self._client,
                    f"{BASE_URL}/ldongCode2",
                    {**_COMMON, "numOfRows": 100},
                    what="시도 코드",
                )
                self._province_codes = {
                    _text(item.get("name")): _text(item.get("code"))
                    for item in _items(payload, what="시도 코드")
                }
        return self._province_codes

    async def _fetch_sites(self, code: str, content_type: str) -> list[RawSite]:
        rows: list[RawSite] = []
        for page in range(1, MAX_PAGES + 1):
            payload = await get_json(
                self._client,
                f"{BASE_URL}/areaBasedList2",
                {
                    **_COMMON,
                    "contentTypeId": content_type,
                    "lDongRegnCd": code,
                    "numOfRows": ROWS,
                    "pageNo": page,
                },
                what="문화시설 목록",
            )
            items = _items(payload, what="문화시설 목록")
            rows.extend(_site(item, content_type) for item in items)
            total = _body(payload).get("totalCount")
            if not items or not isinstance(total, int) or len(rows) >= total:
                return rows
        log.warning("문화시설 목록이 %d쪽을 넘는다 — 뒤는 자른다", MAX_PAGES)
        return rows

    def _roll_day(self) -> None:
        """하루가 지나면 소개정보 · 개요를 비운다 — 주최 측이 고친 연령 · 장소 · 일정을 다음 날에는
        다시 읽는다."""
        today = self._now().astimezone(KST).date()
        if self._detail_day != today:
            self._intros.clear()
            self._overviews.clear()
            self._detail_day = today

    async def _fetch(self, day: date) -> tuple[RawEvent, ...]:
        rows: list[RawEvent] = []
        for page in range(1, MAX_PAGES + 1):
            payload = await get_json(
                self._client,
                f"{BASE_URL}/searchFestival2",
                {
                    **_COMMON,
                    "eventStartDate": day.strftime("%Y%m%d"),
                    "numOfRows": ROWS,
                    "pageNo": page,
                },
                what="행사 목록",
            )
            items = _items(payload, what="행사 목록")
            rows.extend(_event(item) for item in items)
            total = _body(payload).get("totalCount")
            if not items or not isinstance(total, int) or len(rows) >= total:
                return tuple(rows)
        log.warning("행사 목록이 %d쪽을 넘는다 — 뒤는 자른다", MAX_PAGES)
        return tuple(rows)


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    body = (payload.get("response") or {}).get("body") or {}
    return body if isinstance(body, dict) else {}


def _items(payload: dict[str, Any], *, what: str) -> list[dict[str, Any]]:
    """결과 코드를 보고 행 목록을 꺼낸다. 0건이면 `items` 가 빈 문자열로 온다."""
    code = result_code(payload)
    if code != _OK:
        raise UpstreamUnavailable(f"{what} 결과 코드가 {code} 다")
    items = _body(payload).get("items")
    if items in (None, ""):
        return []
    if not isinstance(items, dict):
        raise UpstreamUnavailable(f"{what} 응답 모양이 다르다")
    item = items.get("item") or []
    rows = [item] if isinstance(item, dict) else item
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise UpstreamUnavailable(f"{what} 행 모양이 다르다")
    return rows


def _event(item: dict[str, Any]) -> RawEvent:
    return RawEvent(
        content_id=_text(item.get("contentid")),
        title=_text(item.get("title")),
        start=_text(item.get("eventstartdate")),
        end=_text(item.get("eventenddate")),
        mapx=_text(item.get("mapx")),
        mapy=_text(item.get("mapy")),
        kind_code=_text(item.get("lclsSystm2")),
        progress=_text(item.get("progresstype")),
        region_code=_text(item.get("lDongRegnCd")),
    )


def _site(item: dict[str, Any], content_type: str) -> RawSite:
    return RawSite(
        content_id=_text(item.get("contentid")),
        title=_text(item.get("title")),
        mapx=_text(item.get("mapx")),
        mapy=_text(item.get("mapy")),
        content_type=_text(item.get("contenttypeid")) or content_type,
        category=_text(item.get("lclsSystm2")),
    )


def _text(value: object) -> str:
    """원문 칸을 글자로. 숫자로 온 칸도 글자로 바꾸고, 빈 칸은 "" 다 — 읽는 것은 Activity 다."""
    return "" if value is None else str(value)
