"""`resolve_menu` — 메뉴명을 카탈로그 행으로 바꾼다. 모델이 아니라 코드가 한다.

보호자는 "메뉴" 로 말하고 API 는 "식품" 으로 답한다. 이 변환을 모델에게 맡기면
"크림수프에서 우유 누락" 같은 알레르기 누락과 "단백질 12g" 같은 수치 생성이 생긴다
(Food_Tool_명세 §0-1).

흐름 (`food_agent_own_table.md` §1 · `외부연결_계획.md` §2)
  1. 이름을 `menu_key` 로 정규화한다
  2. `menu_catalog` 를 한 번에 읽는다 (`get_many`)
  3. 없는 키만 영양DB 를 **정확한 이름으로** 조회한다 — 레시피는 요청 중에 찾지 않는다
  4. 받은 행을 카탈로그에 넣는다 (없을 때만. 어댑터가 `ON CONFLICT DO NOTHING`)
  5. 그래도 없으면 unresolved — 영양 계산에서 빠지고 알레르기는 "확인 못 함" 이다

해석 실패는 카탈로그에 남기지 않는다. 정말 없는 이름도 API 장애도 마찬가지다 — 남기면 보호자 말에서
나온 이름이 `child_id` 없이 전역 테이블에 남는다. 대신 못 찾은 이름은 **프로세스 메모리에** 짧게
기억하고(같은 이름을 판정마다 다시 묻지 않게), 판정 한 번의 외부 조회 수에는 상한을 둔다. 장애와
상한 때문에 못 부른 이름은 "없음" 이 아니라서 기억하지 않는다.

외부 API 를 기다리는 동안 DB 트랜잭션을 쥐지 않는다. 조회는 짧은 세션이고, 저장은 결과를 받은 뒤
짧은 트랜잭션 하나다. 취소(`CancelledError`)는 삼키지 않는다 — pipeline 이 20초에 끊을 때
늦은 결과가 성공으로 들어가면 안 된다.

🚨 로그에는 예외 **종류**만 남긴다. 영양DB 는 키가 주소에 들어가고, 메뉴 이름은 보호자 말에서 왔다.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.agents.food.food_groups import food_groups_of_category
from app.agents.food.nutrition_source import NutritionFacts, pick_nutrition
from app.agents.food.store.ports import MenuCatalogRow, MenuCatalogStore, MenuSource
from app.agents.food.tools.safety import menu_codes
from app.rules.menu_key import normalize_menu_key

logger = logging.getLogger(__name__)

# 판정 한 번에 외부로 나가는 조회 수. 처음 보는 이름이 이만큼을 넘으면 나머지는 이번 판정에서
# 건너뛴다. 20초 예산(pipeline) 안에서 동시에 부르므로 한 번 기다리는 시간이 곧 전체 시간이다.
# 잠정값
MAX_LOOKUPS_PER_EVALUATION = 5
# 조회 하나를 기다리는 시간. 이름 검색은 하나씩 4.5 ~ 7.8초, 5개 동시면 중간 6.9초 · 최대
# 15.5초다(10-11 측정. 계획 문서의 0.2 ~ 0.8초는 1 ~ 5행짜리였다). 5개 동시로 30개를 부르면
# 8 ~ 9개가 이 값을 넘는다. 조회는 동시에 나가므로 이 값이 곧 한 번의 최악 대기이고, 20초
# 예산(pipeline) 안에서 모델 호출을 남겨야 해서 늘리지 않는다. 시간 초과는 기억하지 않아
# 다음 판정에서 다시 부른다. 잠정값
LOOKUP_TIMEOUT_S = 8.0
# 못 찾은 이름을 기억하는 시간과 개수. 보호자가 같은 말을 되풀이하는 짧은 동안만 거른다. 잠정값
NOT_FOUND_TTL_S = 1800.0
NOT_FOUND_MAX = 2000

Origin = Literal["catalog", "fetched"]
# empty       이름이 남지 않았다
# not_found   영양DB 에 이름이 정확히 같은 쓸 수 있는 행이 없다 (또는 방금 못 찾아 기억 중)
# source_error API 장애 · 시간 초과 — "없음" 이 아니다
# no_source   조회 포트가 없다 (API 키 없음)
# budget      판정 한 번의 외부 조회 상한 때문에 부르지 않았다
Reason = Literal["empty", "not_found", "source_error", "no_source", "budget"]


@dataclass(frozen=True)
class Resolved:
    """카탈로그 행. `catalog` 는 `resolved` 와 무관하게 있는 행을 그대로 준다.

    해석 여부는 호출부가 `row.resolved` 로 가른다.
    """

    row: MenuCatalogRow
    origin: Origin


@dataclass(frozen=True)
class Unresolved:
    menu_key: str
    reason: Reason


MenuResolution = Resolved | Unresolved


def nutrition_catalog_row(menu_key: str, facts: NutritionFacts, *, now: datetime) -> MenuCatalogRow:
    """영양DB 에서 고른 행 → `menu_catalog` 행. 요청 중 조회와 배치(FT-19)가 이 함수 하나를 쓴다.

    배치가 넣은 행과 요청 중에 넣은 행이 칸 하나라도 다르면 같은 메뉴가 어느 경로로 들어왔느냐에
    따라 다르게 판정된다.
    """
    return MenuCatalogRow(
        menu_key=menu_key,
        display_name=facts.name,
        source="mfds_nutri",
        resolved=True,
        synced_at=now,
        food_code=facts.food_code,
        serving_g=facts.serving_g,
        nutrients=dict(facts.per_100g),
        # 요청 중에는 레시피를 찾지 않는다 — 재료가 비어 있으면 알레르기는 "확인 못 함" 이다.
        # 코드는 메뉴명에서 뽑은 것뿐이다. 배치도 이 칸을 채우지 않는다(레시피 재료는
        # `recipe_catalog` 에만 있다)
        ingredients=(),
        allergen_codes=menu_codes((facts.name,)),
        # 영양DB 의 식품 대분류를 대응표로 옮긴다. 표에 없으면 비어 있다 — 추정하지 않는다
        food_groups=food_groups_of_category(facts.category),
        source_version=facts.source_version,
    )


class NotFoundMemory:
    """영양DB 에서 못 찾은 정규화 이름을 프로세스 메모리에 짧게 기억한다.

    DB · 로그에는 남기지 않는다.
    """

    def __init__(
        self,
        *,
        ttl_s: float = NOT_FOUND_TTL_S,
        max_size: int = NOT_FOUND_MAX,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_size = max_size
        self._clock = clock
        self._until: dict[str, float] = {}

    def remember(self, key: str) -> None:
        self._until.pop(key, None)  # 다시 넣으면 가장 최근 칸으로
        self._until[key] = self._clock() + self._ttl_s
        while len(self._until) > self._max_size:
            self._until.pop(next(iter(self._until)))  # 가장 오래된 것부터 버린다

    def seen(self, key: str) -> bool:
        until = self._until.get(key)
        if until is None:
            return False
        if self._clock() >= until:
            del self._until[key]
            return False
        return True


# 프로세스 전체가 같이 쓰는 기본 기억. 판정(resolver)마다 새로 만들어도 이어진다
PROCESS_NOT_FOUND = NotFoundMemory()


class MenuResolver:
    """한 번의 판정(또는 한 번의 tool 호출)이 쓰는 해석기. 외부 조회 상한은 인스턴스 하나가 센다."""

    def __init__(
        self,
        *,
        catalog: MenuCatalogStore,
        source: MenuSource | None,
        now: datetime,
        not_found: NotFoundMemory = PROCESS_NOT_FOUND,
        max_lookups: int = MAX_LOOKUPS_PER_EVALUATION,
        timeout_s: float = LOOKUP_TIMEOUT_S,
    ) -> None:
        self._catalog = catalog
        self._source = source
        self._now = now
        self._not_found = not_found
        self._lookups_left = max_lookups
        self._timeout_s = timeout_s

    async def resolve(self, name: str) -> MenuResolution:
        key = normalize_menu_key(name)
        return (await self.resolve_many([name]))[key]

    async def resolve_many(self, names: Sequence[str]) -> dict[str, MenuResolution]:
        """여러 이름을 한 번에. 결과는 정규화한 `menu_key` 별이다 — 같은 이름은 한 번만 찾는다.

        카탈로그는 `get_many` 한 번으로 읽고, 없는 키만 외부로 나간다. 외부 조회는 동시에 부르고
        처음 나온 순서대로 상한까지만 나간다.
        """
        results: dict[str, MenuResolution] = {}
        keys: list[str] = []
        for name in names:
            key = normalize_menu_key(name)
            if not key:
                results[""] = Unresolved(menu_key="", reason="empty")
            elif key not in keys:
                keys.append(key)
        if not keys:
            return results

        hits = await self._catalog.get_many(tuple(keys))
        to_fetch: list[str] = []
        for key in keys:
            if key in hits:
                results[key] = Resolved(row=hits[key], origin="catalog")
            elif self._source is None:
                results[key] = Unresolved(menu_key=key, reason="no_source")
            elif self._not_found.seen(key):
                results[key] = Unresolved(menu_key=key, reason="not_found")
            elif self._lookups_left > 0:
                self._lookups_left -= 1
                to_fetch.append(key)
            else:
                results[key] = Unresolved(menu_key=key, reason="budget")

        fetched = await asyncio.gather(*(self._fetch(key) for key in to_fetch))
        results.update(zip(to_fetch, fetched, strict=True))
        # 호출한 순서가 아니라 들어온 순서로 돌려준다
        return {key: results[key] for key in (*keys, *([""] if "" in results else []))}

    async def _fetch(self, key: str) -> MenuResolution:
        assert self._source is not None  # 호출부가 거른다
        try:
            async with asyncio.timeout(self._timeout_s):
                rows = await self._source.nutrition_rows(name=key)
        except Exception as exc:  # 장애 · 시간 초과 — 메시지에 주소(키)가 있을 수 있다
            logger.warning("영양DB 조회 실패 type=%s", type(exc).__name__)
            return Unresolved(menu_key=key, reason="source_error")

        facts = pick_nutrition(rows, key)
        if facts is None:
            self._not_found.remember(key)
            return Unresolved(menu_key=key, reason="not_found")

        row = nutrition_catalog_row(key, facts, now=self._now)
        try:
            await self._catalog.put(row)
        except Exception as exc:  # 저장이 안 돼도 방금 받은 값은 이번 판정에서 쓴다
            logger.warning("메뉴 카탈로그 저장 실패 type=%s", type(exc).__name__)
        return Resolved(row=row, origin="fetched")
