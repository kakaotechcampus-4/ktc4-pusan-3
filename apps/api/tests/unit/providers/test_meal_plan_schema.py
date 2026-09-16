"""app/providers/meal_plan — 급식표 구조화 결과(docs/meal-plan §3)의 모양 검사.

정답셋(178항목) 전체를 검증하는 테스트는 정답셋을 올리는 PR 에서 같이 넣는다.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from app.providers.meal_plan import MealPlanJSON, MealPlanReader, MealPlanSource, MealType


def _minimal(**overrides) -> dict:
    plan = {
        "source": "image",
        "year_month": "2025-01",
        "days": [
            {
                "day": 2,
                "meals": [{"meal_type": "lunch", "items": [{"name": "흑미밥", "raw": "흑미밥"}]}],
            }
        ],
    }
    plan.update(overrides)
    return plan


def test_minimal_plan_validates_with_defaults():
    plan = MealPlanJSON.model_validate(_minimal())

    assert plan.source is MealPlanSource.IMAGE
    assert plan.institution_name is None
    assert plan.unparsed == []
    assert plan.notes is None
    assert plan.days[0].meals[0].meal_type is MealType.LUNCH


def test_rejects_allergen_codes_in_item():
    plan = _minimal()
    plan["days"][0]["meals"][0]["items"][0]["allergen_codes"] = [5, 6]

    with pytest.raises(ValidationError, match="allergen_codes"):
        MealPlanJSON.model_validate(plan)


def test_rejects_item_without_raw():
    plan = _minimal()
    del plan["days"][0]["meals"][0]["items"][0]["raw"]

    with pytest.raises(ValidationError, match="raw"):
        MealPlanJSON.model_validate(plan)


def test_rejects_missing_source():
    plan = _minimal()
    del plan["source"]

    with pytest.raises(ValidationError, match="source"):
        MealPlanJSON.model_validate(plan)


@pytest.mark.parametrize("source", ["pdf", "ocr", ""])
def test_rejects_unknown_source(source):
    with pytest.raises(ValidationError, match="source"):
        MealPlanJSON.model_validate(_minimal(source=source))


@pytest.mark.parametrize("meal_type", ["snack", "간식", "supper"])
def test_rejects_unknown_meal_type(meal_type):
    plan = _minimal()
    plan["days"][0]["meals"][0]["meal_type"] = meal_type

    with pytest.raises(ValidationError, match="meal_type"):
        MealPlanJSON.model_validate(plan)


@pytest.mark.parametrize("day", [0, 32])
def test_rejects_day_outside_range(day):
    plan = _minimal()
    plan["days"][0]["day"] = day

    with pytest.raises(ValidationError, match="day"):
        MealPlanJSON.model_validate(plan)


def test_rejects_day_that_month_does_not_have():
    plan = _minimal(year_month="2025-02")
    plan["days"][0]["day"] = 30

    with pytest.raises(ValidationError, match="2025-02"):
        MealPlanJSON.model_validate(plan)


def test_rejects_same_day_twice():
    plan = _minimal(days=[{"day": 3, "meals": []}, {"day": 3, "meals": []}])

    with pytest.raises(ValidationError, match="두 번"):
        MealPlanJSON.model_validate(plan)


@pytest.mark.parametrize("year_month", ["2025-1", "2025-13", "2025/01", "202501"])
def test_rejects_malformed_year_month(year_month):
    with pytest.raises(ValidationError, match="year_month"):
        MealPlanJSON.model_validate(_minimal(year_month=year_month))


def test_holiday_day_may_have_no_meals_and_a_note():
    plan = MealPlanJSON.model_validate(
        _minimal(days=[{"day": 1, "meals": [], "note": "신정 휴일"}])
    )

    assert plan.days[0].meals == []
    assert plan.days[0].note == "신정 휴일"


def test_unparsed_cell_requires_a_reason():
    with pytest.raises(ValidationError, match="why"):
        MealPlanJSON.model_validate(_minimal(unparsed=[{"day": 4, "meal_type": "snack_pm"}]))

    plan = MealPlanJSON.model_validate(
        _minimal(unparsed=[{"day": 4, "meal_type": "snack_pm", "why": "번호가 흐림"}])
    )
    assert plan.unparsed[0].raw is None


def test_date_of_combines_year_month_and_day():
    plan = MealPlanJSON.model_validate(_minimal(year_month="2026-09"))

    assert plan.date_of(30) == date(2026, 9, 30)


def test_a_reader_only_needs_source_and_read():
    class FakeReader:
        source = MealPlanSource.XLSX

        def read(self, data: bytes, *, mime_type: str) -> MealPlanJSON:
            return MealPlanJSON.model_validate(_minimal(source=self.source))

    reader: MealPlanReader = FakeReader()

    assert reader.read(b"", mime_type="application/octet-stream").source is MealPlanSource.XLSX
