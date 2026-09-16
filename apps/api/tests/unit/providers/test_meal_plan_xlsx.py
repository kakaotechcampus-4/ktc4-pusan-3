"""app/providers/meal_plan/xlsx.py — 센터 배포 엑셀 급식표 읽기.

실제 배포본은 커밋하지 않는다(용량 · 발행처). 실측한 배치를 그대로 흉내 낸 작은 통합문서로 검사한다.
"""

import io

import openpyxl
import pytest

from app.providers.meal_plan import MealPlanSource, MealType
from app.providers.meal_plan.xlsx import (
    XLSX_MIME,
    MealPlanReadError,
    XlsxMealPlanReader,
    list_menu_sheets,
    parse_rows,
)

# 만1~2세 일반형 시트를 흉내 낸 것. 1일이 화요일이라 첫 주 월요일 칸이 비어 있다.
GENERAL_ROWS = [
    (
        "기관명",
        None,
        "2026년 9월 식단표\n(만1~2세 일반형 식단)",
        None,
        "▶발행처 : ○○센터",
        None,
        None,
    ),
    ("요일", "월", "화", "수", "목", "금", "토"),
    ("날짜", None, 1, 2, 3, 4, 5),
    ("오전간식", None, "달걀채소죽:①", "복숭아/치즈:⑪/②", "김치콩나물죽:⑤⑥⑨", "찐고구마/우유:②", 0),
    ("점심", None, "보리밥", "백미밥", "기장밥", "백미밥", "채소볶음밥:⑤⑥"),
    (None, None, "애호박국:⑤⑥", "갈비탕:⑯", "#REF!", "건새우맑은국:⑨", "가쓰오장국:⑤⑥"),
    ("오후간식", None, "소보로빵/우유:①②④⑥/②", 0, "찐단호박/우유:②", 0, 0),
    ("열량(Kcal)/단백질(g)", None, "461.4/16.4", "386/16.2", 0, 0, 0),
    ("날짜", 21, 22, 23, "24:추석연휴", "25:추석", "생일식단"),
    ("오전간식", "바나나/치즈:②", "누룽지죽", "메론/치즈:②", None, None, "키위/치즈:②"),
    ("점심", "흑미밥", "보리밥", "짜장덮밥:⑤⑥⑩", None, None, "잡곡밥"),
    ("열량(Kcal)/단백질(g)", "374.5/14.8", "415.8/16.6", "405.7/17.4", None, None, None),
    ("※알레르기 유발식품(총 19종)", "1.난류 2.우유 ...", None, None, None, None, None),
    ("점심", "각주 뒤는 읽지 않는다", None, None, None, None, None),
]

# 석식형 시트: 라벨에 공백, 끼니는 저녁 하나, 토요일 없음.
DINNER_ROWS = [
    ("기관명", None, "2026년 9월 식단표 (만3~5세 석식형 식단)", None, None, None),
    ("요일", "월", "화", "수", "목", "금", None),
    ("날 짜", None, 1, 2, 3, 4, None),
    ("저 녁", None, "흑미밥", "보리밥", "백미밥", "기장밥", None),
    (None, None, "청경채맑은국", "숙주맑은국", "순두부백탕:①⑤", "유부맑은국:⑤⑥", None),
    ("열량(Kcal)/단백질(g)", None, "216.8/7.2", "202.5/10.4", "253.8/13.6", "235.2/12.2", None),
]


def _workbook(sheets: dict[str, list[tuple]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _day(plan, number):
    return next(d for d in plan.days if d.day == number)


def _items(plan, number, meal_type):
    meal = next(m for m in _day(plan, number).meals if m.meal_type is meal_type)
    return [(item.name, item.raw) for item in meal.items]


def test_reads_title_source_and_blank_institution():
    plan = parse_rows(GENERAL_ROWS)

    assert plan.source is MealPlanSource.XLSX
    assert plan.year_month == "2026-09"
    assert plan.institution_name is None


def test_days_come_from_each_week_header_in_order():
    plan = parse_rows(GENERAL_ROWS)

    assert [d.day for d in plan.days] == [1, 2, 3, 4, 5, 21, 22, 23, 24, 25]


def test_blank_weekday_in_the_first_week_is_not_a_day():
    plan = parse_rows(GENERAL_ROWS)

    assert all(d.day != 0 for d in plan.days)
    assert _items(plan, 1, MealType.SNACK_AM) == [("달걀채소죽", "달걀채소죽:①")]


def test_continuation_rows_belong_to_the_same_meal():
    plan = parse_rows(GENERAL_ROWS)

    assert _items(plan, 1, MealType.LUNCH) == [("보리밥", "보리밥"), ("애호박국", "애호박국:⑤⑥")]


def test_pair_cell_splits_when_names_and_codes_match_in_count():
    plan = parse_rows(GENERAL_ROWS)

    assert _items(plan, 2, MealType.SNACK_AM) == [("복숭아", "복숭아:⑪"), ("치즈", "치즈:②")]
    assert _items(plan, 1, MealType.SNACK_PM) == [
        ("소보로빵", "소보로빵:①②④⑥"),
        ("우유", "우유:②"),
    ]


def test_pair_cell_stays_whole_when_counts_differ():
    plan = parse_rows(GENERAL_ROWS)

    assert _items(plan, 4, MealType.SNACK_AM) == [("찐고구마/우유", "찐고구마/우유:②")]


def test_zero_and_empty_cells_add_nothing():
    plan = parse_rows(GENERAL_ROWS)

    assert [m.meal_type for m in _day(plan, 5).meals] == [MealType.LUNCH]


def test_holiday_keeps_the_note_and_has_no_meals():
    plan = parse_rows(GENERAL_ROWS)

    assert _day(plan, 24).note == "추석연휴"
    assert _day(plan, 24).meals == []
    assert _day(plan, 25).note == "추석"


def test_sample_menu_column_is_not_a_day():
    plan = parse_rows(GENERAL_ROWS)

    assert all("잡곡밥" not in item.raw for d in plan.days for m in d.meals for item in m.items)


def test_excel_error_cell_goes_to_unparsed():
    plan = parse_rows(GENERAL_ROWS)

    assert [(u.day, u.meal_type, u.raw, u.why) for u in plan.unparsed] == [
        (3, MealType.LUNCH, "#REF!", "엑셀 오류값")
    ]
    assert _items(plan, 3, MealType.LUNCH) == [("기장밥", "기장밥")]


def test_stops_at_the_footer():
    plan = parse_rows(GENERAL_ROWS)

    assert all("각주" not in item.raw for d in plan.days for m in d.meals for item in m.items)


def test_dinner_sheet_with_spaced_labels():
    plan = parse_rows(DINNER_ROWS)

    assert [d.day for d in plan.days] == [1, 2, 3, 4]
    assert _items(plan, 3, MealType.DINNER) == [
        ("백미밥", "백미밥"),
        ("순두부백탕", "순두부백탕:①⑤"),
    ]


def test_rejects_sheet_without_title_month():
    rows = [("기관명", None, "식단표"), ("요일", "월", "화"), ("날짜", 1, 2), ("점심", "밥", "죽")]

    with pytest.raises(MealPlanReadError, match="YYYY년 M월"):
        parse_rows(rows)


def test_reader_picks_the_first_menu_sheet_by_default():
    data = _workbook(
        {"영유아 식단안내문": [("2026년 영유아 식단안내",)], "만1~2세 일반형 식단": GENERAL_ROWS}
    )

    plan = XlsxMealPlanReader().read(data, mime_type=XLSX_MIME)

    assert plan.year_month == "2026-09"
    assert list_menu_sheets(data) == ["만1~2세 일반형 식단"]


def test_reader_reads_the_named_sheet():
    data = _workbook({"만1~2세 일반형 식단": GENERAL_ROWS, "만3~5세 석식형 식단": DINNER_ROWS})

    plan = XlsxMealPlanReader(sheet="만3~5세 석식형 식단").read(data, mime_type=XLSX_MIME)

    assert [m.meal_type for m in _day(plan, 1).meals] == [MealType.DINNER]


def test_reader_rejects_other_mime_types():
    with pytest.raises(MealPlanReadError, match="xlsx"):
        XlsxMealPlanReader().read(b"", mime_type="image/jpeg")


def test_reader_rejects_missing_sheet():
    data = _workbook({"만1~2세 일반형 식단": GENERAL_ROWS})

    with pytest.raises(MealPlanReadError, match="시트"):
        XlsxMealPlanReader(sheet="없는 시트").read(data, mime_type=XLSX_MIME)
