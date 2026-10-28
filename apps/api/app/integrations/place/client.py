"""장소 데이터 HTTP 호출 — 공원 · 도서관 표준데이터 · 행안부 어린이놀이시설 (#516)

이 모듈만 세 API 를 안다. 요청 주소 · 쪽수 · 필드 이름 옮기기 · 외부 오류 변환이 여기 모여 있고,
밖으로는 `core/place_raw.py` 의 원문 행(`RawPlaceRow`)만 나간다. 좌표 읽기 · 거르기 · source_id 는
적재 스크립트(`scripts/load_places.py`)가 맡는다 — 여기서는 해석하지 않는다.

- 🚨 전국 목록을 통째로 받을 뿐이다. 보호자 좌표는 이 모듈에 들어오지 않는다 — 반경 질의를 밖에
  맡기지 않으려고 적재한다 (Activity D9).
- 🚨 화이트리스트 — 이름 · 종류 · 좌표 · 데이터기준일자 · 관리번호 · 면적을 옮긴다. 공원 주소
  (lnmadr · rdnmadr)와 유희시설(amsmtFclty)도 옮기지만 적재가 판정(중복 공원 · 놀이터 여부)에만
  쓰고 싣지 않는다. 전화번호(phoneNumber) · 관리기관(institutionNm · insttNm) 칸은 옮기지 않는다.

실제 응답으로 확인한 것 (2026-10-27)
  - 모양은 `{"header": {...}, "body": {"items": {"item": [...]}, "totalCount": ...}}` 이다.
    `response` 로 감싸지 않는다. totalCount · pageNo 는 숫자, 칸 값은 좌표까지 전부 문자열이다.
  - 한 쪽 1,000행이 되고 쪽마다 0.5초 안팎이다. 공원 18,343행(19쪽) · 도서관 3,597행(4쪽).

어린이놀이시설(`getPfctInfo3`)은 모양이 다르다 (2026-10-28 확인)
  - `{"response": {"header": {...}, "body": {"items": [...], "totalCnt": ...}}}` 로 감싸 오고, 쪽
    인자는 `pageIndex` · `recordCountPerPage`, 형식은 `returnType=json` 이다. 한 쪽은 1,000행까지다.
  - 전체 85,388행(86쪽, 쪽마다 2초 안팎)인데 설치장소 코드(`instlPlaceCd`)로 서버에서 거를 수 있다 —
    놀이제공영업소(A013) 1,907행 · 육아종합지원센터(A092) 142행이라 세 쪽이면 된다.

🚨 서비스키는 요청 **주소**에 들어간다. httpx 예외 문구에도 그 주소가 있어서 밖으로는
   `PlaceApiError` 만 나가고, 문구에는 어느 단계에서 실패했는지(예외 종류 · 상태 코드 · 결과 코드)
   까지만 싣는다.
   응답 본문도 싣지 않는다 — `integrations/mfds/client.py` 와 같은 이유다.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import httpx

from app.core.place_raw import RawPlaceRow

PARK_URL = "https://api.data.go.kr/openapi/tn_pubr_public_cty_park_info_api"
LIBRARY_URL = "https://api.data.go.kr/openapi/tn_pubr_public_lbrry_api"
KIDS_URL = "https://apis.data.go.kr/1741000/pfc3/getPfctInfo3"

# 한 쪽에 담는 행 수. 1,000 까지 받는다(실측). 공원이 19쪽이다
PAGE_ROWS = 1000
# 쪽수 상한 — 소스마다 따로 건다. 넘으면 표준데이터가 크게 바뀐 것이라 받지 않고 실패로 올린다.
# 지금은 공원 19쪽 · 도서관 4쪽이다(2026-10-27). 쪽수가 많은 소스(어린이놀이시설은 약 86쪽)는
# 자기 상한을 둔다
PARK_MAX_PAGES = 60
LIBRARY_MAX_PAGES = 60
# 어린이놀이시설은 설치장소 코드마다 받는다 — 지금 A013 2쪽 · A092 1쪽. 서버가 코드 필터를 무시하면
# 전체(86쪽)가 와서 이 상한에 걸린다
KIDS_MAX_PAGES = 10
# 배치라서 넉넉히 기다린다. 실측은 쪽마다 0.5초 안팎이다
BATCH_HTTP_TIMEOUT_S = 60.0

_CODE = re.compile(r"\d{1,3}")


def place_batch_http() -> httpx.AsyncClient:
    """배치용 httpx 클라이언트. `async with` 로 쓰고 서비스키는 `PlaceStdClient` 가 들고 있다."""
    return httpx.AsyncClient(timeout=httpx.Timeout(BATCH_HTTP_TIMEOUT_S, connect=10.0))


class PlaceApiError(Exception):
    """표준데이터 호출이 실패했다 — 연결 · 시간 초과 · HTTP 오류 · 오류 응답 · 응답 모양 불량.

    실패 사유를 나누지 않는다. 적재는 어떤 실패든 아무것도 쓰지 않고 끝난다. 문구는 로그에 그대로
    남을 수 있어서 키 · 주소 · 응답 본문을 담지 않는다.
    """


@dataclass(frozen=True)
class _Paging:
    """API 마다 다른 쪽 넘기기 모양."""

    page: str  # 쪽 번호 인자
    size: str  # 쪽 크기 인자
    total: str  # 전체 행 수 칸
    fmt: tuple[str, str]  # JSON 으로 받는 인자
    wrapped: bool  # `response` 로 감싸 오는가


_STANDARD = _Paging("pageNo", "numOfRows", "totalCount", ("type", "json"), wrapped=False)
_MOIS = _Paging("pageIndex", "recordCountPerPage", "totalCnt", ("returnType", "json"), wrapped=True)


@dataclass(frozen=True)
class PlaceStdClient:
    """공원 · 도서관 · 어린이놀이시설 전체 받기. httpx 클라이언트를 밖에서 받는다.

    테스트는 `httpx.MockTransport` 를 문 `AsyncClient` 를 넣어 서버 없이 돌린다.
    """

    service_key: str
    http: httpx.AsyncClient

    async def parks(self) -> tuple[RawPlaceRow, ...]:
        rows = await self._all(PARK_URL, what="공원", max_pages=PARK_MAX_PAGES)
        return tuple(
            RawPlaceRow(
                name=_text(raw.get("parkNm")),
                kind=_text(raw.get("parkSe")),
                latitude=_text(raw.get("latitude")),
                longitude=_text(raw.get("longitude")),
                reference_date=_text(raw.get("referenceDate")),
                manage_no=_text(raw.get("manageNo")),
                address=_text(raw.get("lnmadr")) or _text(raw.get("rdnmadr")),
                amusement=_text(raw.get("amsmtFclty")),
                area=_text(raw.get("parkAr")),
            )
            for raw in rows
        )

    async def libraries(self) -> tuple[RawPlaceRow, ...]:
        rows = await self._all(LIBRARY_URL, what="도서관", max_pages=LIBRARY_MAX_PAGES)
        return tuple(
            RawPlaceRow(
                name=_text(raw.get("lbrryNm")),
                kind=_text(raw.get("lbrrySe")),
                latitude=_text(raw.get("latitude")),
                longitude=_text(raw.get("longitude")),
                reference_date=_text(raw.get("referenceDate")),
            )
            for raw in rows
        )

    async def kids_facilities(self, *, place_codes: Sequence[str]) -> tuple[RawPlaceRow, ...]:
        """어린이놀이시설 중 설치장소 코드가 `place_codes` 인 것. 코드마다 서버에서 걸러 받는다.

        화이트리스트 — 이름 · 설치장소 코드 · 실내외 코드 · 운영 코드 · 좌표 · 법정동 코드 · 번호만
        옮긴다. 주소 · 설치일 · 민간/공공 칸은 옮기지 않는다. 데이터기준일자 칸은 없다.
        """
        rows: list[dict[str, Any]] = []
        for code in place_codes:
            what = f"어린이놀이시설 {code}"
            found = await self._all(
                KIDS_URL,
                what=what,
                max_pages=KIDS_MAX_PAGES,
                paging=_MOIS,
                params={"instlPlaceCd": code},
            )
            if any(_text(raw.get("instlPlaceCd")) != code for raw in found):
                # 필터가 먹지 않은 채 상한 안에 들어온 경우 — 다른 설치장소가 섞이면 안 된다
                raise PlaceApiError(f"{what} 설치장소 필터가 먹지 않았다")
            rows.extend(found)
        return tuple(
            RawPlaceRow(
                name=_text(raw.get("pfctNm")),
                kind=_text(raw.get("instlPlaceCd")),
                latitude=_text(raw.get("latCrtsVl")),
                longitude=_text(raw.get("lotCrtsVl")),
                reference_date=None,
                manage_no=_text(raw.get("pfctSn")),
                indoor=_text(raw.get("idrodrCd")),
                operating=_text(raw.get("operYnCd")),
                safety_excluded=(_text(raw.get("exfcYn")) or "").upper() == "Y",
                region_code=_text(raw.get("rgnCd")),
            )
            for raw in rows
        )

    async def _all(
        self,
        url: str,
        *,
        what: str,
        max_pages: int,
        paging: _Paging = _STANDARD,
        params: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """쪽마다 차례로 받는다. 한 쪽이라도 실패하거나 받은 행 수가 전체 행 수와 다르면 전체를
        실패로 올린다 — 일부만 받은 채로 적재하면 빠진 곳이 "문 닫은 곳" 으로 지워진다.

        쪽수가 `max_pages` 를 넘으면 첫 쪽만 받고 실패한다. 상한은 소스마다 부르는 쪽이 정한다.
        """
        first = await self._page(url, 1, what=what, paging=paging, params=params)
        total = _total(first, what=what, key=paging.total)
        pages = math.ceil(total / PAGE_ROWS)
        if pages > max_pages:
            raise PlaceApiError(f"{what} 쪽수가 상한을 넘는다 ({pages})")
        items = _items(first, what=what)
        for number in range(2, pages + 1):
            body = await self._page(url, number, what=what, paging=paging, params=params)
            items.extend(_items(body, what=what))
        if len(items) != total:
            raise PlaceApiError(f"{what} 받은 행 수가 다르다 ({len(items)}/{total})")
        return items

    async def _page(
        self,
        url: str,
        page: int,
        *,
        what: str,
        paging: _Paging = _STANDARD,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        query = {
            **(params or {}),
            "serviceKey": _key(self.service_key),
            paging.fmt[0]: paging.fmt[1],
            paging.page: page,
            paging.size: PAGE_ROWS,
        }
        try:
            response = await self.http.get(url, params=query)
        except Exception as exc:  # 연결 · 시간 초과 · 잘못된 주소 — 문구에 요청 주소(키)가 있다
            raise PlaceApiError(f"{what} 요청 실패 {type(exc).__name__}") from None
        if response.status_code != 200:
            raise PlaceApiError(f"{what} HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            # 키가 틀리면 200 에 XML 오류를 준다. 본문을 싣지 않는다
            raise PlaceApiError(f"{what} 응답이 JSON 이 아니다") from None
        if paging.wrapped:
            payload = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or not isinstance(payload.get("header"), dict):
            raise PlaceApiError(f"{what} 응답 모양이 다르다")
        code = payload["header"].get("resultCode")
        if code != "00":
            # 결과 메시지는 싣지 않는다 — 신뢰할 수 없는 글이다. 코드도 숫자 모양일 때만
            shown = code if isinstance(code, str) and _CODE.fullmatch(code) else "?"
            raise PlaceApiError(f"{what} 결과 코드 {shown}")
        body = payload.get("body")
        if not isinstance(body, dict):
            raise PlaceApiError(f"{what} 응답 모양이 다르다")
        return body


def _key(raw: str) -> str:
    """`.env` 의 인증키. Encoding 키(`%2B` 등)면 한 번 푼다 — httpx 가 주소에 넣을 때 다시
    인코딩해서, 풀지 않으면 이중 인코딩으로 거절된다(`integrations/weather/public_data.py` 와
    같다)."""
    key = raw.strip()
    if not key:
        raise PlaceApiError("공공데이터포털 인증키가 없다 (DATA_GO_KR_SERVICE_KEY)")
    return unquote(key) if "%" in key else key


def _total(body: dict[str, Any], *, what: str, key: str = "totalCount") -> int:
    try:
        total = int(body.get(key, 0))
    except (TypeError, ValueError):
        raise PlaceApiError(f"{what} 응답 모양이 다르다") from None
    return max(total, 0)


def _items(body: dict[str, Any], *, what: str) -> list[dict[str, Any]]:
    """`items.item` 의 행들. 한 행이면 목록이 아니라 객체 하나로, 0행이면 빈 글자로 오기도 한다."""
    items = body.get("items")
    if items in (None, "", []):
        return []
    if isinstance(items, dict):
        items = items.get("item")
        if items is None:
            return []
        if isinstance(items, dict):
            items = [items]
    if not isinstance(items, list) or not all(isinstance(raw, dict) for raw in items):
        raise PlaceApiError(f"{what} 응답 모양이 다르다")
    return list(items)


def _text(value: Any) -> str | None:
    """빈 문자열 · 없는 칸은 None. 값은 해석하지 않는다."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
