"""`resolve_menu` — 메뉴명을 카탈로그 행으로. `Food_Tool_명세.md` §0-2 · own_table §1.

가짜 포트(InMemory)만 쓴다. 영양DB 이름 · 값은 전부 가짜다.

지키는 것
  - 카탈로그 적중 / 조회 후 저장 / unresolved / API 실패 네 경우가 갈린다
  - unresolved · API 실패는 카탈로그에 남지 않는다 (보호자 말의 이름이 전역 테이블에 남지 않게)
  - 이름이 다른 결과는 받지 않는다
  - 못 찾은 이름은 프로세스 메모리에 짧게 기억하고, 한 번에 부르는 조회 수에는 상한이 있다
"""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from app.agents.food.menu_resolver import (
    MenuResolver,
    NotFoundMemory,
    Resolved,
    Unresolved,
)
from app.agents.food.store import (
    InMemoryMenuCatalog,
    InMemoryMenuSource,
    MenuCatalogRow,
    MenuSourceError,
)
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow

NOW = datetime(2026, 10, 11, tzinfo=UTC)
_FULL = {key: "10" for key in CORE_NUTRIENT_KEYS}


def raw(name: str, *, code: str = "F1", group: str = "음식", cls: str = "품목대표") -> RawNutriRow:
    return RawNutriRow(
        food_code=code,
        name=name,
        db_group=group,
        db_class=cls,
        category="분류",
        researched_on="20200101",
        food_weight="250g",
        nutrients=_FULL,
    )


def resolver(
    *,
    catalog: InMemoryMenuCatalog | None = None,
    source: InMemoryMenuSource | None = None,
    memory: NotFoundMemory | None = None,
    **kwargs: object,
) -> tuple[MenuResolver, InMemoryMenuCatalog, InMemoryMenuSource]:
    catalog = catalog or InMemoryMenuCatalog()
    source = source or InMemoryMenuSource()
    made = MenuResolver(
        catalog=catalog,
        source=source,
        now=NOW,
        not_found=memory or NotFoundMemory(),
        **kwargs,  # type: ignore[arg-type]
    )
    return made, catalog, source


def catalog_row(key: str = "된장찌개", *, resolved: bool = True) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=key,
        display_name=key,
        source="manual",
        resolved=resolved,
        synced_at=NOW,
    )


# ── 네 경우 ───────────────────────────────────────────────────────────
async def test_카탈로그에_있으면_외부를_부르지_않는다() -> None:
    row = catalog_row()
    made, _, source = resolver(catalog=InMemoryMenuCatalog([row]))

    result = await made.resolve("된장찌개")

    assert result == Resolved(row=row, origin="catalog")
    assert source.calls == []


async def test_카탈로그에_없으면_영양DB를_정확한_이름으로_한_번_조회해_저장한다() -> None:
    source = InMemoryMenuSource(nutrition_rows_by_name={"우유": [raw("우유", code="M1")]})
    made, catalog, _ = resolver(source=source)

    result = await made.resolve("우유")

    assert isinstance(result, Resolved)
    assert result.origin == "fetched"
    assert source.calls == ["우유"]
    row = result.row
    assert row.menu_key == "우유"
    assert row.source == "mfds_nutri"
    assert row.resolved is True
    assert row.food_code == "M1"
    assert row.serving_g == 250.0
    assert row.nutrients["calcium"] == 10.0
    assert row.synced_at == NOW
    # 요청 중에는 레시피를 찾지 않는다 — 재료는 비어 있고, 알레르기는 메뉴명에서 뽑은 코드뿐이다
    assert row.ingredients == ()
    assert 2 in row.allergen_codes  # 우유
    assert await catalog.get("우유") == row


async def test_영양DB에_없으면_unresolved_이고_카탈로그에_남지_않는다() -> None:
    made, catalog, source = resolver()

    result = await made.resolve("처음보는메뉴")

    assert result == Unresolved(menu_key="처음보는메뉴", reason="not_found")
    assert source.calls == ["처음보는메뉴"]
    assert await catalog.get("처음보는메뉴") is None


async def test_API_장애면_unresolved_이고_카탈로그에_남지_않는다() -> None:
    made, catalog, _ = resolver(source=InMemoryMenuSource(fail=True))

    result = await made.resolve("된장찌개")

    assert result == Unresolved(menu_key="된장찌개", reason="source_error")
    assert await catalog.get("된장찌개") is None


async def test_API_장애는_못_찾은_이름으로_기억하지_않는다() -> None:
    # 장애는 "없음" 이 아니다 — 복구되면 다시 찾아야 한다
    source = InMemoryMenuSource(fail=True)
    made, _, _ = resolver(source=source)

    await made.resolve("된장찌개")
    await made.resolve("된장찌개")

    assert source.calls == ["된장찌개", "된장찌개"]


# ── 이름이 정확히 같은 결과만 ─────────────────────────────────────────
async def test_이름이_다른_결과는_받지_않는다() -> None:
    rows = [raw("된장찌개_두부"), raw("부대된장찌개"), raw("된장찌개국")]
    made, catalog, _ = resolver(
        source=InMemoryMenuSource(nutrition_rows_by_name={"된장찌개": rows})
    )

    result = await made.resolve("된장찌개")

    assert result == Unresolved(menu_key="된장찌개", reason="not_found")
    assert await catalog.get("된장찌개") is None


async def test_상용제품만_있으면_받지_않는다() -> None:
    rows = [raw("백미밥", group="가공식품", cls="상용제품")]
    made, catalog, _ = resolver(source=InMemoryMenuSource(nutrition_rows_by_name={"백미밥": rows}))

    result = await made.resolve("백미밥")

    assert isinstance(result, Unresolved)
    assert await catalog.get("백미밥") is None


async def test_영양_칸이_빈_행만_있으면_unresolved() -> None:
    empty = RawNutriRow(
        food_code="X",
        name="된장찌개",
        db_group="음식",
        db_class="품목대표",
        food_weight="250g",
        nutrients={**_FULL, "iron": None},
    )
    made, catalog, _ = resolver(
        source=InMemoryMenuSource(nutrition_rows_by_name={"된장찌개": [empty]})
    )

    assert isinstance(await made.resolve("된장찌개"), Unresolved)
    assert await catalog.get("된장찌개") is None


async def test_외부로_나가는_것은_정규화한_메뉴_이름뿐이다() -> None:
    source = InMemoryMenuSource()
    made, _, _ = resolver(source=source)

    await made.resolve(" 된장 찌개 (2.5.6) ")

    assert source.calls == ["된장찌개"]


async def test_이름이_남지_않으면_외부를_부르지_않는다() -> None:
    made, _, source = resolver()

    assert await made.resolve("(2.5)") == Unresolved(menu_key="", reason="empty")
    assert source.calls == []


async def test_카탈로그에_해석_안_된_행이_있으면_그대로_돌려준다() -> None:
    # 해석 여부는 호출부가 row.resolved 로 가른다 — get 과 같은 계약
    row = catalog_row(resolved=False)
    made, _, source = resolver(catalog=InMemoryMenuCatalog([row]))

    assert await made.resolve("된장찌개") == Resolved(row=row, origin="catalog")
    assert source.calls == []


async def test_조회_포트가_없으면_카탈로그만_쓴다() -> None:
    made = MenuResolver(
        catalog=InMemoryMenuCatalog(), source=None, now=NOW, not_found=NotFoundMemory()
    )

    assert await made.resolve("된장찌개") == Unresolved(menu_key="된장찌개", reason="no_source")


# ── 못 찾은 이름은 짧게 기억한다 ──────────────────────────────────────
async def test_못_찾은_이름은_다시_묻지_않는다() -> None:
    memory = NotFoundMemory()
    made, _, source = resolver(memory=memory)

    first = await made.resolve("처음보는메뉴")
    again = await made.resolve("처음보는 메뉴")  # 표기가 달라도 같은 키

    assert first == Unresolved(menu_key="처음보는메뉴", reason="not_found")
    assert again == Unresolved(menu_key="처음보는메뉴", reason="not_found")
    assert source.calls == ["처음보는메뉴"]


async def test_못_찾은_이름_기억은_resolver_가_바뀌어도_이어진다() -> None:
    # 프로세스 메모리다 — 판정(resolver)마다 새로 만들어도 같은 기억을 쓴다
    memory = NotFoundMemory()
    first, _, source_a = resolver(memory=memory)
    second, _, source_b = resolver(memory=memory)

    await first.resolve("처음보는메뉴")
    await second.resolve("처음보는메뉴")

    assert source_a.calls == ["처음보는메뉴"]
    assert source_b.calls == []


async def test_기억은_짧게만_이어진다() -> None:
    now = [1000.0]
    memory = NotFoundMemory(ttl_s=60.0, clock=lambda: now[0])
    made, _, source = resolver(memory=memory)

    await made.resolve("처음보는메뉴")
    now[0] += 59.0
    await made.resolve("처음보는메뉴")
    assert source.calls == ["처음보는메뉴"]

    now[0] += 2.0  # 기억한 지 61초 — 지났다
    await made.resolve("처음보는메뉴")
    assert source.calls == ["처음보는메뉴", "처음보는메뉴"]


def test_기억하는_이름_수에는_상한이_있다() -> None:
    # 보호자가 매번 다른 이름을 말해도 프로세스 메모리가 끝없이 자라지 않는다
    memory = NotFoundMemory(max_size=3)

    for key in ("가", "나", "다", "라"):
        memory.remember(key)

    assert [memory.seen(key) for key in ("가", "나", "다", "라")] == [False, True, True, True]


async def test_카탈로그에_들어간_이름은_못_찾은_이름이_아니다() -> None:
    memory = NotFoundMemory()
    source = InMemoryMenuSource(nutrition_rows_by_name={"우유": [raw("우유")]})
    made, _, _ = resolver(source=source, memory=memory)

    await made.resolve("우유")

    assert memory.seen("우유") is False


# ── 여러 메뉴를 한 번에: 조회 한 번 · 외부 상한 · 동시 호출 ────────────
class _SpyCatalog(InMemoryMenuCatalog):
    def __init__(self, rows: list[MenuCatalogRow] = ()) -> None:  # type: ignore[assignment]
        super().__init__(rows)
        self.get_many_calls: list[tuple[str, ...]] = []
        self.get_calls = 0

    async def get(self, menu_key: str) -> MenuCatalogRow | None:
        self.get_calls += 1
        return await super().get(menu_key)

    async def get_many(self, menu_keys: tuple[str, ...]) -> dict[str, MenuCatalogRow]:
        self.get_many_calls.append(menu_keys)
        return await super().get_many(menu_keys)


async def test_여러_메뉴는_카탈로그를_한_번에_읽는다() -> None:
    catalog = _SpyCatalog([catalog_row("된장찌개")])
    made, _, _ = resolver(catalog=catalog)

    results = await made.resolve_many(["된장찌개", "미역국", "된장찌개"])

    assert len(catalog.get_many_calls) == 1
    assert catalog.get_calls == 0
    assert set(results) == {"된장찌개", "미역국"}
    assert isinstance(results["된장찌개"], Resolved)


async def test_같은_이름은_판정_한_번에_한_번만_찾는다() -> None:
    source = InMemoryMenuSource(nutrition_rows_by_name={"우유": [raw("우유")]})
    made, _, _ = resolver(source=source)

    results = await made.resolve_many(["우유", "우 유", "우유(200ml)", "우유 2,5"])

    assert source.calls == ["우유"]
    assert set(results) == {"우유"}


async def test_판정_한_번의_외부_조회에는_상한이_있다() -> None:
    names = ["가지볶음", "나물무침", "다시마국", "라면", "마늘종볶음", "바나나", "사과"]
    memory = NotFoundMemory()
    made, _, source = resolver(memory=memory, max_lookups=3)

    results = await made.resolve_many(names)

    assert len(source.calls) == 3
    assert source.calls == ["가지볶음", "나물무침", "다시마국"]  # 처음 나온 순서
    skipped = [key for key, value in results.items() if value == Unresolved(key, "budget")]
    assert skipped == ["라면", "마늘종볶음", "바나나", "사과"]
    # 상한 때문에 못 부른 이름은 "없음" 이 아니다 — 다음 판정에서 다시 찾는다
    assert all(memory.seen(key) is False for key in skipped)


async def test_상한은_카탈로그_적중과_기억한_이름에는_쓰지_않는다() -> None:
    memory = NotFoundMemory()
    memory.remember("기억메뉴")
    catalog = InMemoryMenuCatalog([catalog_row("된장찌개")])
    made, _, source = resolver(catalog=catalog, memory=memory, max_lookups=1)

    results = await made.resolve_many(["된장찌개", "기억메뉴", "새메뉴"])

    assert source.calls == ["새메뉴"]
    assert results["기억메뉴"] == Unresolved("기억메뉴", "not_found")


async def test_외부_조회는_동시에_부른다() -> None:
    in_flight = 0
    peak = 0

    class _SlowSource(InMemoryMenuSource):
        async def nutrition_rows(self, *, name: str) -> tuple[RawNutriRow, ...]:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            in_flight -= 1
            return ()

    made, _, _ = resolver(source=_SlowSource(), max_lookups=5)

    await made.resolve_many(["가", "나", "다"])

    assert peak == 3


async def test_한_메뉴의_장애가_다른_메뉴의_해석을_막지_않는다() -> None:
    class _PartlyBroken(InMemoryMenuSource):
        async def nutrition_rows(self, *, name: str) -> tuple[RawNutriRow, ...]:
            if name == "고장":
                raise MenuSourceError("테스트")
            return await super().nutrition_rows(name=name)

    source = _PartlyBroken(nutrition_rows_by_name={"우유": [raw("우유")]})
    made, _, _ = resolver(source=source)

    results = await made.resolve_many(["고장", "우유"])

    assert results["고장"] == Unresolved("고장", "source_error")
    assert isinstance(results["우유"], Resolved)


async def test_호출이_너무_오래_걸리면_장애로_본다() -> None:
    class _Hanging(InMemoryMenuSource):
        async def nutrition_rows(self, *, name: str) -> tuple[RawNutriRow, ...]:
            await asyncio.sleep(10)
            return ()

    made, catalog, _ = resolver(source=_Hanging(), timeout_s=0.01)

    result = await made.resolve("된장찌개")

    assert result == Unresolved("된장찌개", "source_error")
    assert await catalog.get("된장찌개") is None


async def test_취소는_삼키지_않는다() -> None:
    # pipeline 이 20초에 끊을 때 Food 가 취소를 삼키면 끊긴 Agent 의 늦은 결과가 성공으로 들어간다
    class _Hanging(InMemoryMenuSource):
        async def nutrition_rows(self, *, name: str) -> tuple[RawNutriRow, ...]:
            await asyncio.sleep(10)
            return ()

    made, _, _ = resolver(source=_Hanging(), timeout_s=30.0)

    task = asyncio.create_task(made.resolve("된장찌개"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ── 저장이 실패해도 방금 찾은 값은 이번 run 에서 쓴다 ──────────────────
async def test_저장이_실패해도_찾은_행은_돌려준다() -> None:
    class _BrokenPut(InMemoryMenuCatalog):
        async def put(self, row: MenuCatalogRow) -> None:
            raise RuntimeError("db down")

    source = InMemoryMenuSource(nutrition_rows_by_name={"우유": [raw("우유")]})
    made, _, _ = resolver(catalog=_BrokenPut(), source=source)

    result = await made.resolve("우유")

    assert isinstance(result, Resolved)
    assert result.origin == "fetched"


# ── 로그에 메뉴 이름이 남지 않는다 ────────────────────────────────────
async def test_실패해도_로그에_메뉴_이름을_남기지_않는다(caplog: pytest.LogCaptureFixture) -> None:
    class _BrokenPut(InMemoryMenuCatalog):
        async def put(self, row: MenuCatalogRow) -> None:
            raise RuntimeError("db down 비밀메뉴")

    source = InMemoryMenuSource(nutrition_rows_by_name={"비밀메뉴": [raw("비밀메뉴")]})
    broken_source = InMemoryMenuSource(fail=True)
    with caplog.at_level(logging.DEBUG):
        await resolver(catalog=_BrokenPut(), source=source)[0].resolve("비밀메뉴")
        await resolver(source=broken_source)[0].resolve("비밀메뉴")

    assert caplog.records  # 실패는 알려야 한다
    assert "비밀메뉴" not in caplog.text
