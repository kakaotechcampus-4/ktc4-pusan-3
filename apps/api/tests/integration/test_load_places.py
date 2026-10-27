"""장소 적재 스크립트 — 받기를 다 끝낸 뒤 한 트랜잭션으로 덮고 지운다 (#516).

- 다시 돌려도 행이 늘지 않는다
- 이번 받기에 없는 곳은 지운다(문 닫은 곳)
- 0건이거나 지금 실린 행의 절반이 안 되면 아무것도 쓰지 않는다 — 다른 소스도
- dry-run 은 읽기만 하고, 실제 적재와 같은 수를 센다

장소 이름 · 좌표는 지어낸 값이다.
"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.core.place_raw import RawPlaceRow
from app.domains.place import repository
from app.domains.place.models import Place
from scripts.load_places import Drop, run

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def park(k: int, *, name: str | None = None, date_text: str = "2026-06-30") -> RawPlaceRow:
    return RawPlaceRow(
        name=name or f"시험공원{k}",
        kind="어린이공원",
        latitude=f"{35.10 + k / 1000:.4f}",
        longitude="129.0000",
        reference_date=date_text,
        manage_no=f"11110-{k:05d}",
    )


def library(k: int, *, name: str | None = None) -> RawPlaceRow:
    return RawPlaceRow(
        name=name or f"시험도서관{k}",
        kind="작은도서관",
        latitude=f"{35.20 + k / 1000:.4f}",
        longitude="129.1000",
        reference_date="2026-05-31",
    )


def fetching(*rows: RawPlaceRow):
    async def fetch():
        return list(rows)

    return fetch


async def _never():
    raise AssertionError("고르지 않은 소스는 받지 않는다")


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


@pytest.fixture
def load(opener):
    async def go(*, parks=None, libraries=None, dry_run=False):
        return await run(
            parks=parks is not None,
            libraries=libraries is not None,
            fetch_parks=fetching(*parks) if parks is not None else _never,
            fetch_libraries=fetching(*libraries) if libraries is not None else _never,
            open_session=opener,
            now=NOW,
            dry_run=dry_run,
        )

    return go


async def _count(session, source: str) -> int:
    return await session.scalar(
        select(func.count()).select_from(Place).where(Place.source == source)
    )


async def _names(session, source: str) -> list[str]:
    rows = await session.scalars(
        select(Place.name).where(Place.source == source).order_by(Place.source_id)
    )
    return list(rows.all())


async def test_공원_도서관을_한_번에_싣는다(session, load):
    loaded = await load(parks=[park(1), park(2)], libraries=[library(1)])

    assert [(item.source, item.kept, item.stored, item.deleted) for item in loaded] == [
        ("city_park", 2, 0, 0),
        ("library", 1, 0, 0),
    ]
    stored = await session.scalar(select(Place).where(Place.source_id == "11110-00001"))
    assert stored.category == "park"
    assert stored.sigungu_code == "11110"
    assert stored.data_date == date(2026, 6, 30)
    assert stored.source_version == "tn_pubr_public_cty_park_info_api"
    lib = await session.scalar(select(Place).where(Place.source == "library"))
    assert (lib.category, lib.sigungu_code) == ("library", None)


async def test_다시_돌려도_행이_늘지_않는다(session, load):
    rows = [park(1), park(2), park(3)]
    await load(parks=rows)

    again = await load(parks=rows)

    assert await _count(session, "city_park") == 3
    assert (again[0].stored, again[0].deleted) == (3, 0)


async def test_이번_받기에_없는_곳은_지운다(session, load):
    await load(parks=[park(1), park(2), park(3)], libraries=[library(1)])

    loaded = await load(parks=[park(1), park(3), park(4)])

    assert loaded[0].deleted == 1
    assert await _names(session, "city_park") == ["시험공원1", "시험공원3", "시험공원4"]
    assert await _count(session, "library") == 1  # 이번에 받지 않은 소스는 건드리지 않는다


async def test_같은_번호는_가장_새로운_행이_실린다(session, load):
    loaded = await load(
        parks=[park(1, name="옛 이름", date_text="2025-01-31"), park(1, name="새 이름")]
    )

    assert await _names(session, "city_park") == ["새 이름"]
    assert loaded[0].dropped == {Drop.DUPLICATE: 1}


async def test_0건이면_아무것도_쓰지_않는다(session, load):
    await load(parks=[park(1), park(2)], libraries=[library(1)])

    with pytest.raises(RuntimeError, match="0건"):
        await load(parks=[], libraries=[library(1, name="바뀐 이름")])

    assert await _count(session, "city_park") == 2
    assert await _names(session, "library") == ["시험도서관1"]  # 다른 소스도 쓰지 않았다


async def test_지금_실린_행의_절반이_안_되면_아무것도_쓰지_않는다(session, load):
    await load(parks=[park(k) for k in range(1, 6)], libraries=[library(1)])

    with pytest.raises(RuntimeError, match="절반"):
        await load(parks=[park(1), park(2)], libraries=[library(1, name="바뀐 이름")])

    assert await _count(session, "city_park") == 5
    assert await _names(session, "library") == ["시험도서관1"]  # 다른 소스도 쓰지 않았다


async def test_딱_절반이면_싣는다(session, load):
    await load(parks=[park(k) for k in range(1, 5)])

    loaded = await load(parks=[park(1), park(2)])

    assert loaded[0].deleted == 2
    assert await _count(session, "city_park") == 2


async def test_dry_run_은_쓰지_않고_실제와_같은_수를_센다(session, load):
    await load(parks=[park(1), park(2), park(3)])

    dry = await load(parks=[park(1), park(3), park(4)], dry_run=True)

    assert await _names(session, "city_park") == ["시험공원1", "시험공원2", "시험공원3"]
    real = await load(parks=[park(1), park(3), park(4)])
    assert dry == real
    assert (dry[0].kept, dry[0].stored, dry[0].deleted) == (3, 3, 1)


async def test_도서관_번호는_다시_받아도_같다(session, load):
    await load(libraries=[library(1), library(2)])

    loaded = await load(libraries=[library(1), library(2)])

    assert loaded[0].deleted == 0
    assert await repository.stored_ids(session, source="library") == {
        "시험도서관1|35.2010|129.1000",
        "시험도서관2|35.2020|129.1000",
    }
