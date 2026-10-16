"""식약처 식품영양성분DB HTTP 호출 — data.go.kr 15127578 (FoodNtrCpntDbInfo03)

이 모듈만 영양DB 서버를 안다. 요청 주소 · 필터 · 쪽수 · 필드 이름 옮기기 · 외부 오류 변환이 여기
모여 있고, 밖으로는 `core/mfds_nutri_raw.py` 의 원문 행(`RawNutriRow`)만 나간다. 어느 행을 쓸지
(이름이 정확히 같은지 · 품목대표인지 · 값이 다 찼는지)와 숫자로 바꾸는 것은 Food
`nutrition_source.py` 가 맡는다 — 여기서는 해석하지 않는다.

실제 응답으로 확인한 것 (2026-10-11)
  - `FOOD_NM_KR` 은 **포함 검색**이다. 우유는 2,809행이고 첫 100행에 정확히 같은 이름이 없다.
  - `DB_CLASS_NM=품목대표` 필터는 먹힌다(우유 2,809 → 33행). `DB_GRP_NM` · `DB_GRP_CM` 필터는
    무시된다. 그래서 요청에는 품목대표만 건다 — 상용제품(포장 제품)은 어차피 메뉴명에 쓰지 않는다.
  - 결과는 `NUM` 오름차순이고 이름이 같은 행이 결과 여기저기에 흩어진다(미역국 56행 중 5행).
  - 영양값은 `AMT_NUM1` ~ `AMT_NUM157` 번호뿐이고 이름이 없다. 아래 `NUTRIENT_FIELDS` 는 응답으로
    검증했다 — 열량 = 단백질·탄수화물 4 + 지방 9 kcal/g 가 맞고(된장찌개 45.95 ≈ 46), 우유의
    당류 4.81(유당) · 칼슘 118 · 나트륨 40, 버터의 지방 82.04 · 포화지방 48.05 가 상식과 맞다.
    포화지방은 `AMT_NUM24` 이고 이웃한 `AMT_NUM23` 은 콜레스테롤(우유 10.13 · 버터 232.12)이다.
  - 응답 시간은 페이지 크기에 따라 다르다. 1 ~ 5행은 약 1초, 100행은 3 ~ 4초이고, 느린 때는 10초를
    넘기도 한다(17초 관측). 호출하는 쪽이 timeout 을 건다.
  - 이름 없이 품목대표만 받으면 100행 1 ~ 4초 · 한 번에 최대 500행 · 전체 8,807행(own_table FT-19).
  - 품목대표 전체에서 원재료성 3,605행 중 514행은 `FOOD_CAT1_NM` 이 비고 `FOOD_CAT1_CD` 만 온다
    (`09` 육류 · `10` 난류 · `13` 우유 및 유제품류 · `12` 해조류). 코드 뜻은 DB 그룹마다 다르다.

🚨 서비스키는 요청 **주소**에 들어간다. httpx 예외 문구(연결 실패 · 시간 초과)에도 그 주소가
   있다. 그래서 밖으로는 `MfdsApiError` 만 나가고, 문구에는 어느 단계에서 실패했는지(예외 종류 ·
   상태 코드 · 결과 코드)까지만 싣는다. 원인 예외는 `from None` 으로 끊는다. 응답 본문도 싣지
   않는다 — 키가 틀리면 오류 본문에 키가 되돌아올 수 있다. `integrations/discord.py` 와 같은
   이유다.
"""

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.mfds_nutri_raw import RawNutriRow

BASE_URL = "https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03/getFoodNtrCpntDbInq03"

# 한 쪽에 담는 행 수. 품목대표 필터를 건 흔한 메뉴명은 대부분 100행 안이다(미역국 56 · 잡채 40).
# 500 까지 받지만(1,000 을 요청하면 오류가 빈 결과로 온다) 클수록 느리다
PAGE_ROWS = 100
# 쪽수 상한. 흔한 낱말("국" 608행 · "밥" 432행)은 여러 쪽이지만 그런 낱말은 정확히 같은 이름이
# 없다. 상한을 넘는 행은 받지 않고 Agent 가 못 찾으면 unresolved 로 둔다
MAX_PAGES = 3
# 메뉴명에 쓰는 분류. 상용제품 · 외식은 받지 않는다 (nutrition_source 와 같은 값)
REPRESENTATIVE_CLASS = "품목대표"
# 응답을 기다리는 시간(연결은 절반). 100행은 보통 3 ~ 4초이고 느린 때는 10초를 넘는다. 호출하는 쪽
# (Food `resolve_menu`)이 이보다 짧은 호출별 timeout 으로 먼저 끊는다 — 이 값은 마지막 안전망이다
HTTP_TIMEOUT_S = 10.0
# 품목대표 전체 받기(배치, FT-19). 한 번에 최대 500행이다(1,000 을 요청하면 오류가 빈 결과로 온다).
# 8,807행이면 18쪽이다. 쪽수가 상한을 넘으면 영양DB 가 크게 바뀐 것이라 받지 않고 실패로 올린다
BULK_ROWS = 500
BULK_MAX_PAGES = 40
# 배치는 요청 경로가 아니라서 넉넉히 기다린다. 500행은 10초를 넘기기도 한다
BATCH_HTTP_TIMEOUT_S = 60.0

# 내부 영양소 이름(`core/mfds_nutri_raw.NUTRIENT_KEYS`) → API 칸. 실제 응답으로 검증했다
NUTRIENT_FIELDS: dict[str, str] = {
    "energy": "AMT_NUM1",
    "protein": "AMT_NUM3",
    "fat": "AMT_NUM4",
    "carbohydrate": "AMT_NUM6",
    "sugars": "AMT_NUM7",
    "calcium": "AMT_NUM9",
    "iron": "AMT_NUM10",
    "sodium": "AMT_NUM13",
    "saturated_fat": "AMT_NUM24",
}

_CODE = re.compile(r"\d{1,3}")


def mfds_nutri_http() -> httpx.AsyncClient:
    """운영용 httpx 클라이언트. `async with` 로 쓰고 서비스키는 `MfdsNutriClient` 가 들고 있다."""
    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT_S, connect=HTTP_TIMEOUT_S / 2))


def mfds_batch_http() -> httpx.AsyncClient:
    """배치용 httpx 클라이언트 — 요청 경로의 `mfds_nutri_http` 보다 오래 기다린다."""
    return httpx.AsyncClient(timeout=httpx.Timeout(BATCH_HTTP_TIMEOUT_S, connect=10.0))


class MfdsApiError(Exception):
    """영양DB 호출이 실패했다 — 연결 · 시간 초과 · HTTP 오류 · 오류 응답 · 응답 모양 불량.

    실패 사유를 나누지 않는다. 호출하는 쪽은 어떤 실패든 그 메뉴를 unresolved 로 두고(알레르기는
    "확인 못 함") 카탈로그에 남기지 않는다. 문구는 로그에 그대로 남을 수 있어서 키 · 주소 · 응답
    본문을 담지 않는다.
    """


@dataclass(frozen=True)
class MfdsNutriClient:
    """영양DB 조회. httpx 클라이언트를 밖에서 받는다.

    테스트는 `httpx.MockTransport` 를 문 `AsyncClient` 를 넣어 서버 없이 돌린다. 조회 하나가
    여러 쪽을 동시에 받아서 클라이언트는 연결을 나눠 쓸 수 있어야 한다. timeout 은 그 클라이언트에
    건다(`mfds_nutri_http()`) — 이 호출은 느릴 수 있어 Agent 쪽 호출별 timeout 이 한 번 더 묶는다.
    """

    service_key: str
    http: httpx.AsyncClient

    async def nutrition_rows(self, *, name: str) -> tuple[RawNutriRow, ...]:
        """`name` 이 이름에 들어간 품목대표 행들. 이름이 다른 행(된장찌개_냉이)도 섞여 있다.

        없으면 빈 튜플이고 장애가 아니다. 일부 쪽이 실패하면 앞쪽만 돌려주지 않고 전체를 실패로
        올린다 — 정확히 같은 이름의 행이 빠진 채로 "없음" 처럼 보이면 안 된다.
        """
        if not name.strip():
            return ()
        first = await self._page(name, 1)
        total = _total(first)
        pages = min(MAX_PAGES, math.ceil(total / PAGE_ROWS))
        rest = await asyncio.gather(
            *(self._page(name, number) for number in range(2, pages + 1)), return_exceptions=True
        )
        bodies = [first]
        for result in rest:
            if isinstance(result, BaseException):
                raise result  # MfdsApiError 이거나 취소다
            bodies.append(result)
        return tuple(_row(raw) for body in bodies for raw in _items(body))

    async def representative_rows(self) -> tuple[RawNutriRow, ...]:
        """품목대표 전체 — 배치(FT-19)만 부른다. 쪽마다 차례로 받는다(동시에 부르면 느린 때
        시간 초과가 몰린다). 한 쪽이라도 실패하거나 받은 행 수가 `totalCount` 와 다르면 전체를
        실패로 올린다 — 일부만 실으면 요청 중 조회는 찾는 메뉴가 배치에서는 빠진다."""
        first = await self._bulk_page(1)
        total = _total(first)
        pages = math.ceil(total / BULK_ROWS)
        if pages > BULK_MAX_PAGES:
            raise MfdsApiError(f"mfds 품목대표 쪽수가 상한을 넘는다 ({pages})")
        bodies = [first]
        for number in range(2, pages + 1):
            bodies.append(await self._bulk_page(number))
        rows = tuple(_row(raw) for body in bodies for raw in _items(body))
        if len(rows) != total:
            raise MfdsApiError(f"mfds 받은 행 수가 다르다 ({len(rows)}/{total})")
        return rows

    async def _bulk_page(self, page: int) -> dict[str, Any]:
        return await self._request(
            {
                "serviceKey": self.service_key,
                "type": "json",
                "DB_CLASS_NM": REPRESENTATIVE_CLASS,
                "pageNo": page,
                "numOfRows": BULK_ROWS,
            }
        )

    async def _page(self, name: str, page: int) -> dict[str, Any]:
        return await self._request(
            {
                "serviceKey": self.service_key,
                "type": "json",
                "FOOD_NM_KR": name,
                "DB_CLASS_NM": REPRESENTATIVE_CLASS,
                "pageNo": page,
                "numOfRows": PAGE_ROWS,
            }
        )

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.http.get(BASE_URL, params=params)
        except (
            Exception
        ) as exc:  # 연결 · 시간 초과 · 잘못된 주소 — 문구에 요청 주소(키)가 들어 있다
            raise MfdsApiError(f"mfds 요청 실패 {type(exc).__name__}") from None
        if response.status_code != 200:
            raise MfdsApiError(f"mfds HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            # 키가 틀리면 200 에 XML 오류를 준다. 본문을 싣지 않는다
            raise MfdsApiError("mfds 응답이 JSON 이 아니다") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("header"), dict):
            raise MfdsApiError("mfds 응답 모양이 다르다")
        code = payload["header"].get("resultCode")
        if code != "00":
            # 결과 메시지는 싣지 않는다 — 신뢰할 수 없는 글이다. 코드도 숫자 모양일 때만
            shown = code if isinstance(code, str) and _CODE.fullmatch(code) else "?"
            raise MfdsApiError(f"mfds 결과 코드 {shown}")
        body = payload.get("body")
        if not isinstance(body, dict):
            raise MfdsApiError("mfds 응답 모양이 다르다")
        return body


def _total(body: dict[str, Any]) -> int:
    try:
        total = int(body.get("totalCount", 0))
    except (TypeError, ValueError):
        raise MfdsApiError("mfds 응답 모양이 다르다") from None
    return max(total, 0)


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if items is None:
        return []
    if not isinstance(items, list) or not all(isinstance(raw, dict) for raw in items):
        raise MfdsApiError("mfds 응답 모양이 다르다")
    return items


def _text(value: Any) -> str | None:
    """빈 문자열 · 없는 칸은 None. 값은 해석하지 않는다."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row(raw: dict[str, Any]) -> RawNutriRow:
    return RawNutriRow(
        food_code=_text(raw.get("FOOD_CD")) or "",
        name=_text(raw.get("FOOD_NM_KR")) or "",
        db_group=_text(raw.get("DB_GRP_NM")) or "",
        db_class=_text(raw.get("DB_CLASS_NM")) or "",
        category=_text(raw.get("FOOD_CAT1_NM")),
        category_code=_text(raw.get("FOOD_CAT1_CD")),
        ref_name=_text(raw.get("FOOD_REF_NM")),
        source=_text(raw.get("SUB_REF_NAME")),
        researched_on=_text(raw.get("RESEARCH_YMD")),
        food_weight=_text(raw.get("Z10500")),
        nutrients={key: _text(raw.get(field)) for key, field in NUTRIENT_FIELDS.items()},
    )
