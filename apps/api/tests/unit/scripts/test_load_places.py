"""장소 적재 스크립트의 거르기 · 번호 · 출력 — DB 없이 (#516).

이름 · 좌표는 지어낸 값이다. 실제 장소를 넣지 않는다. DB 에 쓰는 흐름(덮기 · 지우기 · 거절)은
tests/integration/test_load_places.py 가 본다.
"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest

from app.agents.activity.schemas.common import PlaceCategory
from app.core.place_raw import RawPlaceRow
from app.domains.place.models import KOREA_BOX_CHECK
from scripts.load_places import (
    KOREA_LAT,
    KOREA_LNG,
    LIBRARY_KINDS,
    Drop,
    Loaded,
    build_parser,
    clean,
    has_playground,
    in_korea,
    library_source_id,
    parse_area,
    parse_coord,
    parse_data_date,
    place_values,
    run,
    sigungu_code,
    summary,
)

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def park(no: str | None = "11110-00001", **over) -> RawPlaceRow:
    values = {
        "name": "시험공원",
        "kind": "어린이공원",
        "latitude": "35.1000",
        "longitude": "129.0000",
        "reference_date": "2026-06-30",
        "manage_no": no,
    }
    return RawPlaceRow(**values | over)


def library(name: str = "시험도서관", **over) -> RawPlaceRow:
    values = {
        "name": name,
        "kind": "공공도서관",
        "latitude": "35.2000",
        "longitude": "129.1000",
        "reference_date": "2026-05-31",
    }
    return RawPlaceRow(**values | over)


# ── 좌표 · 날짜 · 번호 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [("35.1234", 35.1234), (" 129.5 ", 129.5), (None, None), ("", None), ("abc", None)],
)
def test_좌표를_읽는다(text, expected):
    assert parse_coord(text) == expected


@pytest.mark.parametrize("text", ["nan", "inf", "-inf"])
def test_유한하지_않은_좌표는_못_읽은_것이다(text):
    assert parse_coord(text) is None


@pytest.mark.parametrize(
    ("lat", "lng", "inside"),
    [
        (35.1, 129.0, True),
        (33.0, 124.0, True),  # 경계는 안이다
        (39.0, 132.0, True),
        (129.0, 35.1, False),  # 위경도가 뒤바뀐 행
        (0.0, 0.0, False),
        (32.9, 126.5, False),
        (37.5, 132.1, False),
    ],
)
def test_한국_상자(lat, lng, inside):
    assert in_korea(lat, lng) is inside


def test_한국_상자는_DB_CHECK_와_같다():
    # 적재가 거르는 상자와 표가 막는 상자가 다르면, 걸러 놓고도 적재가 CHECK 에 걸려 통째로 실패한다
    lat_min, lat_max = (int(v) for v in KOREA_LAT)
    lng_min, lng_max = (int(v) for v in KOREA_LNG)
    assert KOREA_BOX_CHECK == (
        f"lat BETWEEN {lat_min} AND {lat_max} AND lng BETWEEN {lng_min} AND {lng_max}"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-06-30", date(2026, 6, 30)),
        (" 2026-06-30 ", date(2026, 6, 30)),
        ("2026-13-01", None),
        ("", None),
        (None, None),
        ("기준일 없음", None),
    ],
)
def test_데이터기준일자를_읽는다(text, expected):
    assert parse_data_date(text) == expected


@pytest.mark.parametrize(
    ("manage_no", "expected"),
    [
        ("11110-00001", "11110"),
        (" 47999-00001 ", "47999"),
        ("1111-000001", None),  # 앞 5자리가 숫자가 아니다
        ("111100-0001", None),  # 숫자 6자리 — 시군구 코드 모양이 아니다
        ("ABCDE-00001", None),
        ("11110", None),
        ("", None),
        (None, None),
    ],
)
def test_시군구_코드는_관리번호_앞_5자리_숫자다(manage_no, expected):
    assert sigungu_code(manage_no) == expected


def test_도서관_번호는_이름과_소수_넷째_자리_좌표다():
    assert library_source_id("시험도서관", 35.123456, 129.000049) == "시험도서관|35.1235|129.0000"


def test_좌표가_넷째_자리_아래만_다르면_같은_번호다():
    a = library_source_id("시험도서관", 35.12341, 129.00001)
    b = library_source_id("시험도서관", 35.12342, 129.00002)
    assert a == b


def test_이름이_같아도_자리가_다르면_다른_번호다():
    assert library_source_id("시험작은도서관", 35.1, 129.0) != library_source_id(
        "시험작은도서관", 35.2, 129.0
    )


# ── 거르기 ────────────────────────────────────────────────────────────
def test_공원_행을_적재_행으로_옮긴다():
    [record] = clean("city_park", [park()]).records

    assert record.source == "city_park"
    assert record.source_id == "11110-00001"
    assert record.category is PlaceCategory.PARK
    assert record.sigungu_code == "11110"
    assert (record.lat, record.lng) == (35.1, 129.0)
    assert record.data_date == date(2026, 6, 30)
    assert record.has_playground is True  # 어린이공원
    assert record.area_m2 is None  # 면적 칸이 없다


def test_도서관은_시군구_코드를_비운다():
    [record] = clean("library", [library()]).records

    assert record.category is PlaceCategory.LIBRARY
    assert record.source_id == "시험도서관|35.2000|129.1000"
    assert record.sigungu_code is None
    assert (record.has_playground, record.area_m2) == (False, None)


# ── 놀이터 여부 · 면적 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("kind", "amusement", "expected"),
    [
        ("어린이공원", None, True),
        (" 어린이공원 ", "", True),
        ("근린공원", "그네, 미끄럼틀", True),  # 유희시설이 있는 공원
        ("소공원", "놀이터", True),
        ("근린공원", None, False),
        ("근린공원", "   ", False),
        ("근린공원", "해당없음", False),  # 실측 12행 — 빈 칸이다
        ("근린공원", "해당 없음", False),
        (None, None, False),
    ],
)
def test_놀이터는_어린이공원이거나_유희시설이_있는_공원이다(kind, amusement, expected):
    assert has_playground(kind, amusement) is expected


def test_공원_행에_놀이터_여부와_면적을_싣는다():
    rows = [
        park("11110-00001", kind="근린공원", amusement="그네", area="12500.5"),
        park("11110-00002", kind="근린공원", amusement=None, area="800"),
    ]

    records = clean("city_park", rows).records

    assert [(r.has_playground, r.area_m2) for r in records] == [(True, 12500.5), (False, 800.0)]


def test_도서관은_놀이터_칸을_보지_않는다():
    [record] = clean("library", [library(kind="어린이도서관", amusement="놀이방")]).records

    assert record.has_playground is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1500", 1500.0),
        (" 2345.67 ", 2345.67),
        ("0", None),  # 넓이를 모른다(실측 13행)
        ("-5", None),
        ("", None),
        (None, None),
        ("약 1500", None),
        ("nan", None),
    ],
)
def test_면적을_읽는다(text, expected):
    assert parse_area(text) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (park(name=None), Drop.NO_NAME),
        (park(name="   "), Drop.NO_NAME),
        (park(no=None), Drop.NO_ID),
        (park(latitude=None), Drop.NO_COORDS),
        (park(longitude="동경 129도"), Drop.NO_COORDS),
        (park(latitude="129.0", longitude="35.1"), Drop.OUTSIDE_KOREA),
        (park(latitude="0", longitude="0"), Drop.OUTSIDE_KOREA),
    ],
)
def test_공원에서_버리는_행(raw, reason):
    result = clean("city_park", [raw])

    assert result.records == ()
    assert result.dropped == {reason: 1}


@pytest.mark.parametrize("kind", ["학교도서관", "대학도서관", "전문도서관", "장애인도서관", None])
def test_보호자가_들어갈_수_없는_도서관은_뺀다(kind):
    assert clean("library", [library(kind=kind)]).dropped == {Drop.KIND_EXCLUDED: 1}


@pytest.mark.parametrize("kind", sorted(LIBRARY_KINDS))
def test_공공_작은_어린이도서관은_싣는다(kind):
    assert len(clean("library", [library(kind=kind)]).records) == 1


def test_공원은_묘지공원만_뺀다():
    kinds = ["근린공원", "기타", None, "묘지공원"]
    rows = [park(f"11110-0000{k}", kind=kind) for k, kind in enumerate(kinds)]

    result = clean("city_park", rows)

    assert len(result.records) == 3
    assert result.dropped == {Drop.KIND_EXCLUDED: 1}


# ── 관리번호가 겹친 공원 — 같은 곳이면 하나, 다른 공원이면 따로 싣는다 ─────────
# 위도 0.001도는 약 111m 다
def test_번호가_같고_멀리_떨어진_다른_이름의_공원은_따로_싣는다():
    # 기관이 번호를 새로 매겨 같은 번호가 다른 공원을 가리키는 경우(광주 · 대구 · 인천 실측)
    rows = [
        park(name="새공원", reference_date="2026-08-01", address="시험시 가동 1"),
        park(
            name="옛공원", reference_date="2026-06-30", latitude="35.0900", address="시험시 나동 9"
        ),
    ]

    result = clean("city_park", rows)

    assert sorted(record.source_id for record in result.records) == [
        "11110-00001",
        "11110-00001|옛공원",
    ]
    assert result.dropped == {}


def test_가까운_곳의_같은_이름은_종류_말이_달라도_중복이다():
    rows = [
        park(name="수랑", reference_date="2026-08-01"),
        park(name="수랑어린이공원", reference_date="2026-06-30", latitude="35.1010"),
    ]

    result = clean("city_park", rows)

    assert [record.name for record in result.records] == ["수랑"]
    assert result.dropped == {Drop.DUPLICATE: 1}


def test_다른_번호로_다시_올라온_같은_공원도_중복이다():
    # 옛 행의 번호 주인은 다른 공원이지만, 옛 행과 같은 공원이 다른 번호로 이미 실려 있다
    rows = [
        park("11110-00001", name="새공원", reference_date="2026-08-01"),
        park("11110-00002", name="옛공원", reference_date="2026-08-01", latitude="35.0900"),
        park("11110-00001", name="옛공원", reference_date="2026-06-30", latitude="35.0901"),
    ]

    result = clean("city_park", rows)

    assert sorted(record.source_id for record in result.records) == ["11110-00001", "11110-00002"]
    assert result.dropped == {Drop.DUPLICATE: 1}


def test_시도_이름이_바뀌어도_지번이_같으면_중복이다():
    rows = [
        park(name="대성공원", reference_date="2026-08-01", address="통합특별시 남구 진월동 878"),
        park(
            name="진월제1어린이공원",
            reference_date="2026-06-30",
            latitude="35.1050",
            address="광주광역시 남구 진월동 878",
        ),
    ]

    assert [record.name for record in clean("city_park", rows).records] == ["대성공원"]


def test_이름만_바뀐_같은_자리는_중복이다():
    rows = [
        park(name="신안어린이공원", reference_date="2026-08-01"),
        park(name="신용제1어린이공원", reference_date="2026-06-30", latitude="35.1003"),
    ]

    assert [record.name for record in clean("city_park", rows).records] == ["신안어린이공원"]


def test_주소와_유희시설_원문은_싣지_않는다():
    [record] = clean("city_park", [park(address="시험시 가동 1", amusement="그네")]).records

    values = place_values(record, now=NOW)

    assert "address" not in values
    assert "그네" not in values.values()
    assert values["has_playground"] is True


def test_같은_번호는_데이터기준일자가_가장_새로운_행만_남긴다():
    rows = [
        park(name="옛 이름", reference_date="2025-01-31"),
        park(name="새 이름", reference_date="2026-06-30"),
        park(name="더 옛 이름", reference_date="2019-09-30"),
    ]

    result = clean("city_park", rows)

    assert [record.name for record in result.records] == ["새 이름"]
    assert result.dropped == {Drop.DUPLICATE: 2}


def test_날짜가_같거나_모르면_먼저_온_행을_둔다():
    rows = [
        park(name="먼저", reference_date="2026-06-30"),
        park(name="나중", reference_date="2026-06-30"),
        park(name="날짜 모름", reference_date=None),
    ]

    assert [record.name for record in clean("city_park", rows).records] == ["먼저"]


def test_날짜를_모르는_행은_날짜가_있는_행에_밀린다():
    rows = [park(name="날짜 모름", reference_date=""), park(name="날짜 있음")]

    assert [record.name for record in clean("city_park", rows).records] == ["날짜 있음"]


def test_같은_자리의_같은_도서관은_하나만_남는다():
    rows = [library(reference_date="2025-12-31"), library(reference_date="2026-05-31")]

    result = clean("library", rows)

    assert [record.data_date for record in result.records] == [date(2026, 5, 31)]
    assert result.dropped == {Drop.DUPLICATE: 1}


def test_받은_행은_남긴_행과_버린_행의_합이다():
    rows = [park(), park(), park(no="11110-00002", latitude=""), park(no="11110-00003")]

    result = clean("city_park", rows)

    assert result.received == 4
    assert len(result.records) + sum(result.dropped.values()) == 4


def test_적재_값에는_화이트리스트_칸만_있다():
    [record] = clean("city_park", [park()]).records

    values = place_values(record, now=NOW)

    assert set(values) == {
        "source",
        "source_id",
        "name",
        "category",
        "lat",
        "lng",
        "sigungu_code",
        "data_date",
        "has_playground",
        "area_m2",
        "source_version",
        "synced_at",
    }
    assert values["category"] == "park"
    assert values["source_version"] == "tn_pubr_public_cty_park_info_api"
    assert values["synced_at"] == NOW


# ── run(): 쓰기 전에 막는 것 ──────────────────────────────────────────
def _never_open():
    @asynccontextmanager
    async def opener():
        raise AssertionError("DB 를 열기 전에 막았어야 한다")
        yield

    return opener


async def _rows(*rows: RawPlaceRow):
    return list(rows)


@pytest.mark.parametrize(
    "rows",
    [
        [],  # API 가 오류를 빈 결과로 줬다
        [park(latitude=""), park(no="11110-00002", name="")],  # 다 걸러졌다
    ],
)
async def test_남은_행이_0건이면_DB_를_열지_않는다(rows):
    async def fetch():
        return rows

    with pytest.raises(RuntimeError, match="0건"):
        await run(
            parks=True,
            libraries=True,
            fetch_parks=fetch,
            fetch_libraries=lambda: _rows(library()),
            open_session=_never_open(),
            now=NOW,
        )


async def test_받다가_실패하면_DB_를_열지_않는다():
    async def broken():
        raise RuntimeError("표준데이터 장애")

    with pytest.raises(RuntimeError, match="장애"):
        await run(
            parks=True,
            libraries=True,
            fetch_parks=lambda: _rows(park()),
            fetch_libraries=broken,
            open_session=_never_open(),
            now=NOW,
        )


async def test_고르지_않은_소스는_받지_않는다():
    async def never():
        raise AssertionError("--parks 만 돌릴 때는 도서관을 받지 않는다")

    with pytest.raises(RuntimeError, match="0건"):
        await run(
            parks=True,
            libraries=False,
            fetch_parks=lambda: _rows(),
            fetch_libraries=never,
            open_session=_never_open(),
            now=NOW,
        )


# ── 출력 ──────────────────────────────────────────────────────────────
LOADED = [
    Loaded(
        source="city_park",
        received=18343,
        kept=16941,
        dropped={Drop.OUTSIDE_KOREA: 1, Drop.DUPLICATE: 1401},
        stored=16950,
        deleted=9,
    ),
    Loaded(
        source="library",
        received=3597,
        kept=3381,
        dropped={Drop.NO_COORDS: 104, Drop.KIND_EXCLUDED: 33, Drop.DUPLICATE: 79},
        stored=0,
        deleted=0,
    ),
]


@pytest.mark.parametrize("dry_run", [True, False])
def test_요약은_cp949_콘솔에서도_찍힌다(dry_run):
    text = summary(LOADED, dry_run=dry_run)

    text.encode("cp949")
    assert ("dry-run" in text) is dry_run
    assert ("지울 행 9" in text) is dry_run
    assert ("지운 행 9" in text) is not dry_run


def test_요약은_소스마다_받은_남긴_버린_행을_사유별로_센다():
    text = summary(LOADED, dry_run=False)

    parks, libraries = text.splitlines()
    assert parks.startswith("city_park: 받음 18343 · 남김 16941 · 버림 1402(한국 밖 1 · 중복 1401)")
    assert libraries.startswith(
        "library: 받음 3597 · 남김 3381 · 버림 216(좌표 없음 104 · 제외 종류 33 · 중복 79)"
    )


def test_버린_행이_없으면_없음이라고_쓴다():
    loaded = Loaded(source="library", received=1, kept=1, dropped={}, stored=0, deleted=0)

    assert "버림 0(없음)" in summary([loaded], dry_run=False)


def test_도움말은_cp949_콘솔에서도_찍힌다():
    build_parser().format_help().encode("cp949")
