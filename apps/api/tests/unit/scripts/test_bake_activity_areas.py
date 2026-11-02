"""격자 표 → 시군구 대표 지점 굽기. 엑셀 대신 행을 직접 넣는다 (openpyxl 없이 돈다)."""

import pytest

from scripts.bake_activity_areas import parse_districts, render
from scripts.bake_holidays import BakeError

HEADER = ("구분", "행정구역코드", "1단계", "2단계", "3단계", "격자 X", "격자 Y")


def row(code, province, district, town="", lon="129.165808333333", lat="35.1600194444444"):
    # 격자 · 도분초 칸은 쓰지 않는다. 경도 · 위도는 14 · 15번째 칸(초/100)이다
    return ("kor", code, province, district, town, "99", "75", "", "", "", "", "", "", lon, lat)


ROWS = [
    HEADER,
    row("2600000000", "부산광역시", ""),  # 시도 행은 굽지 않는다
    row("2635000000", "부산광역시", "해운대구"),
    row("2635051000", "부산광역시", "해운대구", "우1동"),  # 읍면동 행도
    row("4111100000", "경기도", "수원시장안구", lon="127.0101", lat="37.3040"),
    row("5019000000", "이어도", "", lon="0", lat="0"),
]


def test_시군구_행만_굽고_위경도를_싣는다():
    assert parse_districts(ROWS) == [
        ("부산광역시", "해운대구", 35.16, 129.1658),
        ("경기도", "수원시장안구", 37.304, 127.0101),
    ]


def test_모르는_시도면_멈춘다():
    # 행정구역이 바뀐 것이다 — 지역 이름표(PROVINCE_ALIASES)를 먼저 고친다
    with pytest.raises(BakeError, match="모르는 시도"):
        parse_districts([HEADER, row("9911000000", "새특별시", "새구")])


def test_한국_밖_좌표면_멈춘다():
    with pytest.raises(BakeError, match="좌표"):
        parse_districts(
            [HEADER, row("2635000000", "부산광역시", "해운대구", lon="35.16", lat="129.16")]
        )


def test_시군구가_0곳이면_멈춘다():
    with pytest.raises(BakeError, match="0곳"):
        parse_districts([HEADER])


def test_ruff_format_이_고치지_않는_모양으로_쓴다():
    text = render([("부산광역시", "해운대구", 35.16, 129.1658)], source="격자_위경도(2610)")
    assert '    ("부산광역시", "해운대구", 35.16, 129.1658),\n' in text
    assert "격자_위경도(2610)" in text
