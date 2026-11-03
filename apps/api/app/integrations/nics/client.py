"""농촌진흥청 「농식품 이달의 식재료」 레시피 HTTP 호출 — 공공데이터포털(apis.data.go.kr).

월별 목록(`recipeList`)을 12번 받아 레시피 번호를 모으고, 번호마다 상세(`recipeCont`)를 받아
이름과 재료 문자열(`matrlInfo`)을 `core/mafra_recipe_raw.py` 의 원문 모양으로 옮긴다. **배치에서만**
부른다 — 요청 중에는 부르지 않는다. 재료명 정리 · 알레르기 코드 · 식품군은 정리 단계(AI)가 맡고,
재료 문자열을 자르는 규칙은 `core/recipe_materials.py` 에 있다.

확인한 것 (실제 호출 2026-10-18 · 공개 안내 페이지)
  - 주소는 `https://apis.data.go.kr/1390803/allbaroFoodMonth/{오퍼레이션}` 이고 키(`serviceKey`)는
    **쿼리**로 보낸다. `format=json`.
  - `recipeList` 는 `srchMonth` 가 필수이고 `srchFdmtNm` 을 **빈 값으로 보내면 그 달 전체**다.
    달마다 41 ~ 69건이고 `numOfRows=1000` 이면 `totalCount` 와 같은 수가 한 번에 온다. 12달을
    합쳐 고유 `recipeSn` 598건.
  - `recipeCont` 는 `srchRecipeSn` 으로 부르고 `body.stdInfo{recipeSn, recipeNm, matrlInfo, ...}`
    가 온다. `recipeSn` 은 안내에는 정수, 실제 응답은 글자(`"55"`)라 둘 다 받아 글자로 쓴다.
  - 이용허락범위는 "제한 없음", 개발 계정 하루 10,000건.

실제 응답 모양(2026-11-02 키로 확인)은 `{response: {header: {resultCode: "200", resultMsg}, body}}` 이고,
목록은 `body.items.item`(목록)과 `body.totalCount`(글자), 상세는 `body.stdInfo` 다. 성공 코드가
"200" 이라는 것만 처음 가정("00")과 달랐다. 오류 응답의 코드 값은 확인하지 못했다 — 성공 코드가
아니면 코드만 싣고 멈춘다. 모양이 다르면 "응답 모양이 다르다" 로 멈춘다 — 조용히 틀린 데이터를
적재하지 않는다.

받은 수가 `totalCount` 와 다르거나, 어느 달이든 목록이 비거나, 상세가 어긋나면 하나도 돌려주지 않고
`NicsApiError` 로 올린다. 일부만 적재하면 재료를 덜 아는 레시피가 생기고, 비어 온 달의 레시피가 후보
풀에서 조용히 내려간다. 배치는 실패하면 이전 적재본을 그대로 둔다. 재료 문자열이 깨진 레시피는
다르다 — 그 레시피만 이름 없는 재료로 둬서 후보에서 빼고(resolved=false) 경고에 번호를 남긴다. 한
건 때문에 전체를 멈추면 농식품정보원 · 영양DB 적재까지 같이 막힌다.

🚨 키가 요청 주소에 있고 httpx 예외 문구 · INFO 로그에도 그 주소가 있다. 밖으로는 `NicsApiError` 만
나가고 문구에는 예외 종류 · 상태 코드 · 결과 코드(와 레시피 번호)까지만 싣는다. 결과 메시지는 싣지
않는다(키가 되돌아올 수 있다). 받는 동안 httpx 로거를 WARNING 으로 올려 요청 주소가 로그에 남지
않게 한다 — 서버는 `configure_logging()` 이 막지만 배치 스크립트는 그 경로를 거치지 않는다.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import Coroutine, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx

from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync
from app.core.recipe_materials import split_materials

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

BASE_URL = "https://apis.data.go.kr/1390803/allbaroFoodMonth"
LIST_OPERATION = "recipeList"
DETAIL_OPERATION = "recipeCont"

# 한 번에 받는 목록 행 수. 달마다 69건 이하라 1,000이면 한 번에 다 온다
ROWS_PER_CALL = 1000
# 상세 호출 상한. 고유 레시피가 598건이다 — 이보다 많으면 데이터가 크게 바뀐 것이라 받지 않는다
MAX_DETAIL_CALLS = 1000
# 동시에 부르는 수. 실제 API 는 짧은 시간에 몰리면 429 를 준다(2026-11-02 실측: 동시 5 로 몰면 약
# 20건 뒤부터 거절, 초당 5건 안팎의 지속 호출은 120건까지 통과). 목록 12 + 상세 598 이 1분 남짓이다
CONCURRENCY = 2
# 429 를 받으면 이만큼씩 쉬었다가 다시 부른다(점점 길게). 다 쉬고도 안 되면 멈춘다. 몰렸던 제한은
# 1분 안에 풀렸다
RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
# 응답을 기다리는 시간. 배치 전용이라 길게 둔다
HTTP_TIMEOUT_S = 30.0

_MONTHS = range(1, 13)
# 성공 코드. 실제 응답은 "200" 이다(2026-11-02 확인). "00" 은 공공데이터포털 표준이라 같이 받는다
_OK_CODES = frozenset({"200", "00"})
# 화면에 찍을 수 있는 ASCII 만 — 공백 · 제어문자 · 한글이 든 키는 부르지 않는다. 일반
# 인증키(Decoding)는 `+` `/` `=` 가 섞이는데, 쿼리로 보내면 httpx 가 인코딩해 준다
_KEY = re.compile(r"[\x21-\x7e]{1,256}")
_CODE = re.compile(r"[0-9A-Za-z_-]{1,8}")


class NicsApiError(Exception):
    """레시피 호출이 실패했다 — 연결 · 시간 초과 · HTTP 오류 · 결과 코드 · 응답 모양 · 건수 불일치.

    문구는 로그에 그대로 남을 수 있어서 키 · 주소 · 결과 메시지 · 재료 원문을 담지 않는다.
    """


def nics_recipe_http() -> httpx.AsyncClient:
    """배치용 httpx 클라이언트. `async with` 로 쓰고 키는 `NicsRecipeClient` 가 들고 있다."""
    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT_S, connect=10.0))


@dataclass(frozen=True)
class NicsRecipeClient:
    """이달의 식재료 레시피 전체 동기화. httpx 클라이언트를 밖에서 받는다."""

    # repr 에서 뺀다 — traceback 이 지역 변수를 찍거나 로그가 객체를 찍어도 키가 남지 않게
    api_key: str = field(repr=False)
    http: httpx.AsyncClient

    async def recipes_with_ingredients(self) -> RecipeSync:
        """12달 목록과 상세를 다 받아 레시피로 옮긴다. 하나라도 어긋나면 `NicsApiError`."""
        if not self.api_key:
            raise NicsApiError("nics 인증키가 없다")
        if not _KEY.fullmatch(self.api_key):
            raise NicsApiError("nics 인증키 모양이 다르다")
        with _quiet_httpx_log():
            numbers = await self._listed()
            recipes = await self._details(numbers)
        ingredients = [item for recipe in recipes for item in recipe.ingredients]
        return RecipeSync(
            recipes=tuple(recipes),
            recipe_rows=len(numbers),
            ingredient_rows=len(ingredients),
            orphan_ingredient_rows=0,  # 상세 한 건 안에 재료가 들어 있어 떨어지는 행이 없다
            unnamed_ingredient_rows=sum(1 for item in ingredients if item.name is None),
        )

    async def _listed(self) -> list[str]:
        """12달 목록에서 고유 레시피 번호를 번호 순으로. 같은 레시피가 여러 달에 있을 수 있다.

        달은 서로 독립이라 동시에 부른다 — 차례로 부르면 느린 응답이 그대로 쌓인다.
        """
        gate = asyncio.Semaphore(CONCURRENCY)

        async def month_items(month: int) -> list[dict[str, Any]]:
            async with gate:
                body = await self._call(
                    LIST_OPERATION,
                    {"srchMonth": month, "srchFdmtNm": "", "numOfRows": ROWS_PER_CALL},
                )
            items = _items(body)
            total = _total(body)
            if len(items) != total:
                raise NicsApiError(f"nics {month}월 받은 목록 수가 다르다 ({len(items)}/{total})")
            if not items:
                raise NicsApiError(f"nics {month}월 목록이 비었다")
            return items

        numbers: dict[str, None] = {}
        for items in await _all_or_none(month_items(month) for month in _MONTHS):
            for item in items:
                numbers.setdefault(_number(item.get("recipeSn")), None)
        if len(numbers) > MAX_DETAIL_CALLS:
            raise NicsApiError(f"nics 레시피 수가 상한을 넘는다 ({len(numbers)})")
        return sorted(numbers, key=int)

    async def _details(self, numbers: list[str]) -> list[RawRecipe]:
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(number: str) -> RawRecipe:
            async with gate:
                body = await self._call(DETAIL_OPERATION, {"srchRecipeSn": number})
            return _recipe(number, body)

        return await _all_or_none(one(number) for number in numbers)

    async def _call(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {"serviceKey": self.api_key, "format": "json", **params}
        for delay in (*RETRY_DELAYS_S, None):
            response = await self._get(operation, query)
            if response.status_code != 429 or delay is None:
                break
            logger.warning("nics 429 — %s초 쉬었다가 다시 부른다", delay)
            await asyncio.sleep(delay)
        if response.status_code != 200:
            raise NicsApiError(f"nics HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:  # 키 오류는 JSON 이 아니라 XML 로 오기도 한다
            raise NicsApiError("nics 응답이 JSON 이 아니다") from None
        if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
            payload = payload["response"]  # 공공데이터포털이 한 번 더 감싸는 모양도 받는다
        if not isinstance(payload, dict):
            raise NicsApiError("nics 응답 모양이 다르다")
        _check(payload.get("header"))
        body = payload.get("body")
        if not isinstance(body, dict):
            raise NicsApiError("nics 응답 모양이 다르다")
        return body

    async def _get(self, operation: str, query: dict[str, Any]) -> httpx.Response:
        try:
            return await self.http.get(f"{BASE_URL}/{operation}", params=query)
        except Exception as exc:  # 연결 · 시간 초과 — 문구에 주소(키)가 들어 있다
            raise NicsApiError(f"nics 요청 실패 {type(exc).__name__}") from None


async def _all_or_none(calls: Iterable[Coroutine[Any, Any, _T]]) -> list[_T]:
    """모두 받거나 하나도 안 받는다. 하나가 실패하면 나머지를 멈추고 그 하나를 올린다.
    결과는 넘긴 순서 그대로다."""
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(call) for call in calls]
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return [task.result() for task in tasks]


@contextlib.contextmanager
def _quiet_httpx_log() -> Iterator[None]:
    """받는 동안 httpx 가 요청 주소(키 포함)를 INFO 로 남기지 못하게 한다. 끝나면 원래대로."""
    logger = logging.getLogger("httpx")
    before = logger.level
    if logger.getEffectiveLevel() < logging.WARNING:
        logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(before)


def _check(header: Any) -> None:
    """정상 코드가 아니면 코드만 싣고 올린다. 결과 메시지는 싣지 않는다."""
    code = header.get("resultCode") if isinstance(header, dict) else None
    if isinstance(code, int) and not isinstance(code, bool):
        code = f"{code:02d}"
    if code in _OK_CODES:
        return
    shown = code if isinstance(code, str) and _CODE.fullmatch(code) else "?"
    raise NicsApiError(f"nics 결과 코드 {shown}")


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """목록 응답에서 항목을 꺼낸다. 실제 응답은 `items.item` 목록이다 — 목록 · 한 건 객체도 받는다."""
    raw = body.get("items")
    if isinstance(raw, dict):  # `items.item` 으로 한 겹 더 감싼 모양
        raw = raw.get("item")
    if isinstance(raw, dict):  # 한 건이면 목록이 아니라 객체로 오기도 한다
        raw = [raw]
    if raw is None or raw == "":
        return []
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        raise NicsApiError("nics 응답 모양이 다르다")
    return raw


def _total(body: dict[str, Any]) -> int:
    value = body.get("totalCount")
    if isinstance(value, bool):
        raise NicsApiError("nics 응답 모양이 다르다")
    try:
        return max(int(value), 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise NicsApiError("nics 응답 모양이 다르다") from None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> str:
    """`recipeSn` 을 글자로. 정수로 오든 글자로 오든 같고, 정수가 아니면 응답 모양이 다른 것이다."""
    if isinstance(value, bool):
        raise NicsApiError("nics 응답 모양이 다르다")
    text = _text(value)
    if text is None or not (text.isascii() and text.isdigit()):
        raise NicsApiError("nics 응답 모양이 다르다")
    return str(int(text))


def _recipe(number: str, body: dict[str, Any]) -> RawRecipe:
    info = body.get("stdInfo")
    if not isinstance(info, dict):
        raise NicsApiError("nics 응답 모양이 다르다")
    if _number(info.get("recipeSn")) != number:
        raise NicsApiError("nics 상세 번호가 목록과 다르다")
    name = _text(info.get("recipeNm"))
    if name is None:
        raise NicsApiError("nics 응답 모양이 다르다")
    # 칸이 없으면 응답 모양이 바뀐 것이다 — 모든 레시피가 재료 없음으로 읽히면 후보 풀이 통째로
    # 내려간다
    if "matrlInfo" not in info or not (
        info["matrlInfo"] is None or isinstance(info["matrlInfo"], str)
    ):
        raise NicsApiError("nics 응답 모양이 다르다")
    try:
        ingredients = split_materials(info["matrlInfo"] or "")
    except ValueError:
        # 그 레시피만 이름 없는 재료로 둔다 — 후보에서 빠지고 나머지는 그대로 실린다. 경고에는
        # 번호만 남기고 원문은 싣지 않는다
        logger.warning("nics 재료 문자열 형식이 다르다 (번호 %s) — 모르는 재료로 둔다", number)
        ingredients = (RawIngredient(name=None),)
    return RawRecipe(recipe_id=number, name=name, ingredients=ingredients)
