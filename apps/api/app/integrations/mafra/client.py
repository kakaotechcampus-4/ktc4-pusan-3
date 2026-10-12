"""농식품정보원 레시피 HTTP 호출 — 농림축산식품 공공데이터포털(data.mafra.go.kr)

레시피 기본정보(537건)와 재료정보(6,104행)를 전부 받아 `RECIPE_ID` 로 잇는다. **배치에서만**
부른다 — 요청 중에는 부르지 않는다(하루 호출 한도가 서비스 전체 상한이 된다). 밖으로는
`core/mafra_recipe_raw.py` 의 원문 모양만 나가고, 재료명 정리 · 알레르기 코드 · 식품군은 정리
단계(AI)가 맡는다.

실제 응답으로 확인한 것 (`sample` 키, 2026-10-11)
  - 주소는 `{BASE_URL}/{키}/json/{서비스}/{시작}/{끝}` 이다. 키가 **경로**에 들어가고 평문 HTTP 다.
  - 성공은 `{서비스: {totalCnt, startRow, endRow, result: {code: "INFO-000"}, row: [...]}}` 다.
  - 오류는 HTTP 200 에 서비스 이름 없이 `{result: {code, message}}` 만 온다 — 잘못된 키 `INFO-100`,
    시작이 끝보다 큼 `ERROR-334`, sample 키 한도 초과 `ERROR-335`.
  - `RECIPE_ID` · `IRDNT_SN` 은 정수로 온다.

실제 키로 전체 동기화한 결과 (2026-10-11) — 호출 8회 · 1.9초. 기본정보 537건 · 재료 6,104행.
기본정보의 RECIPE_ID 는 1 ~ 500 다음 큰 번호로 건너뛰고, 재료 171행이 기본정보에 없는 RECIPE_ID
38개(501 ~ 540)를 가리켜 버린다. 이름이 빈 재료 행 0 · 재료가 없는 레시피 0 · 서로 다른 재료
이름 715.

전체 동기화는 서비스마다 1,000행씩 차례로 받는다(기본 1회 + 재료 7회). 받은 행 수가 `totalCnt` 와
다르거나 응답 모양이 다르면 실패로 올린다 — 일부만 적재하면 재료를 덜 아는 레시피가 생겨 알레르기를
놓친다. 배치는 실패하면 이전 적재본을 그대로 둔다.

🚨 키가 주소 경로에 있고 httpx 예외 문구에도 그 주소가 있다. 밖으로는 `MafraApiError` 만 나가고
   문구에는 예외 종류 · 상태 코드 · 결과 코드까지만 싣는다. 결과 메시지는 싣지 않는다(키가
   되돌아올 수 있다).
   키에 경로를 바꿀 글자("/" · "?")가 있으면 부르지 않는다.
"""

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync

BASE_URL = "http://211.237.50.150:7080/openapi"
RECIPE_SERVICE = "Grid_20150827000000000226_1"  # 레시피 기본정보
INGREDIENT_SERVICE = "Grid_20150827000000000227_1"  # 레시피 재료정보

# 한 번에 받는 행 수. 일반 키는 1,000행까지 받는다(sample 키는 5행)
ROWS_PER_CALL = 1000
# 서비스당 호출 상한. 재료 6,104행이면 7회다. 이보다 많으면 데이터가 크게 바뀐 것이라 받지 않는다
MAX_CALLS = 10
# 응답을 기다리는 시간. 배치 전용이라 길게 둔다(10-11 오전에 연결이 끊긴 적이 있다)
HTTP_TIMEOUT_S = 30.0

_OK = "INFO-000"
_NO_DATA = "INFO-200"  # 플랫폼 안내 코드 — 해당하는 데이터가 없다
_KEY = re.compile(r"[A-Za-z0-9]{1,128}")
_CODE = re.compile(r"[A-Z]{3,6}-\d{3}")


class MafraApiError(Exception):
    """레시피 호출이 실패했다 — 연결 · 시간 초과 · HTTP 오류 · 오류 코드 · 응답 모양 · 행 수 불일치.

    문구는 로그에 그대로 남을 수 있어서 키 · 주소 · 결과 메시지를 담지 않는다.
    """


def mafra_recipe_http() -> httpx.AsyncClient:
    """배치용 httpx 클라이언트. `async with` 로 쓰고 키는 `MafraRecipeClient` 가 들고 있다."""
    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT_S, connect=10.0))


@dataclass(frozen=True)
class MafraRecipeClient:
    """레시피 기본정보 · 재료정보 전체 동기화. httpx 클라이언트를 밖에서 받는다."""

    api_key: str
    http: httpx.AsyncClient

    async def recipes_with_ingredients(self) -> RecipeSync:
        """기본정보와 재료를 다 받아 `RECIPE_ID` 로 잇는다. 하나라도 어긋나면 `MafraApiError`."""
        if not self.api_key:
            raise MafraApiError("mafra 인증키가 없다")
        if not _KEY.fullmatch(self.api_key):
            raise MafraApiError("mafra 인증키 모양이 다르다")
        recipe_rows = await self._all(RECIPE_SERVICE)
        ingredient_rows = await self._all(INGREDIENT_SERVICE)
        return _join(recipe_rows, ingredient_rows)

    async def _all(self, service: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        total: int | None = None
        start = 1
        for _ in range(MAX_CALLS):
            end = start + ROWS_PER_CALL - 1
            body = await self._page(service, start, end)
            if body is None:  # 데이터가 없다
                break
            if total is None:
                total = _total(body)
                if total > ROWS_PER_CALL * MAX_CALLS:
                    raise MafraApiError(f"mafra 행 수가 상한을 넘는다 ({total})")
            rows.extend(_rows(body))
            if end >= total:
                break
            start = end + 1
        if len(rows) != (total or 0):
            raise MafraApiError(f"mafra 받은 행 수가 다르다 ({len(rows)}/{total})")
        return rows

    async def _page(self, service: str, start: int, end: int) -> dict[str, Any] | None:
        url = f"{BASE_URL}/{self.api_key}/json/{service}/{start}/{end}"
        try:
            response = await self.http.get(url)
        except Exception as exc:  # 연결 · 시간 초과 — 문구에 주소(키)가 들어 있다
            raise MafraApiError(f"mafra 요청 실패 {type(exc).__name__}") from None
        if response.status_code != 200:
            raise MafraApiError(f"mafra HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise MafraApiError("mafra 응답이 JSON 이 아니다") from None
        if not isinstance(payload, dict):
            raise MafraApiError("mafra 응답 모양이 다르다")
        body = payload.get(service)
        if body is None:
            # 오류는 서비스 이름 없이 result 만 온다
            _check(payload.get("result"))
            return None
        if not isinstance(body, dict):
            raise MafraApiError("mafra 응답 모양이 다르다")
        return body if _check(body.get("result")) else None


def _check(result: Any) -> bool:
    """정상이면 True, 데이터가 없으면 False. 그 밖의 코드는 코드만 싣고 올린다."""
    code = result.get("code") if isinstance(result, dict) else None
    if code == _OK:
        return True
    if code == _NO_DATA:
        return False
    shown = code if isinstance(code, str) and _CODE.fullmatch(code) else "?"
    raise MafraApiError(f"mafra 결과 코드 {shown}")


def _total(body: dict[str, Any]) -> int:
    value = body.get("totalCnt")
    if isinstance(value, bool):
        raise MafraApiError("mafra 응답 모양이 다르다")
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        raise MafraApiError("mafra 응답 모양이 다르다") from None


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    rows = body.get("row") or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise MafraApiError("mafra 응답 모양이 다르다")
    return rows


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _id(value: Any) -> str:
    """RECIPE_ID 를 글자로. 없거나 정수가 아니면 응답 모양이 다른 것이다."""
    if isinstance(value, bool):
        raise MafraApiError("mafra 응답 모양이 다르다")
    text = _text(value)
    if text is None or not text.isdigit():
        raise MafraApiError("mafra 응답 모양이 다르다")
    return str(int(text))


def _seq(value: Any) -> int | None:
    text = _text(value)
    return int(text) if text is not None and text.isdigit() else None


def _join(recipe_rows: list[dict[str, Any]], ingredient_rows: list[dict[str, Any]]) -> RecipeSync:
    heads: dict[str, dict[str, Any]] = {}
    for raw in recipe_rows:
        recipe_id = _id(raw.get("RECIPE_ID"))
        if _text(raw.get("RECIPE_NM_KO")) is None:
            raise MafraApiError("mafra 응답 모양이 다르다")
        if recipe_id in heads:
            raise MafraApiError("mafra RECIPE_ID 가 겹친다")
        heads[recipe_id] = raw

    grouped: dict[str, list[tuple[tuple[bool, int, int], RawIngredient]]] = {}
    orphans = unnamed = 0
    for index, raw in enumerate(ingredient_rows):
        recipe_id = _id(raw.get("RECIPE_ID"))
        if recipe_id not in heads:
            orphans += 1  # 기본정보가 없는 재료 행 — 붙일 레시피가 없다
            continue
        name = _text(raw.get("IRDNT_NM"))
        if name is None:
            unnamed += 1  # 버리지 않는다 — 모르는 재료로 남겨 그 레시피가 후보에서 빠지게 한다
        seq = _seq(raw.get("IRDNT_SN"))
        order = (seq is None, seq or 0, index)
        item = RawIngredient(
            name=name, amount=_text(raw.get("IRDNT_CPCTY")), role=_text(raw.get("IRDNT_TY_NM"))
        )
        grouped.setdefault(recipe_id, []).append((order, item))

    recipes = tuple(
        RawRecipe(
            recipe_id=recipe_id,
            name=_text(raw.get("RECIPE_NM_KO")) or "",
            nation=_text(raw.get("NATION_NM")),
            kind=_text(raw.get("TY_NM")),
            ingredients=tuple(item for _, item in sorted(grouped.get(recipe_id, []))),
        )
        for recipe_id, raw in heads.items()
    )
    return RecipeSync(
        recipes=recipes,
        recipe_rows=len(recipe_rows),
        ingredient_rows=len(ingredient_rows),
        orphan_ingredient_rows=orphans,
        unnamed_ingredient_rows=unnamed,
    )
