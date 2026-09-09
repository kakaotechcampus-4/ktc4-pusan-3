"""app/providers/meal_ocr/schema.py — 급식표 OCR 출력(docs/meal-plan §3)의 모양 검사."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.providers.meal_ocr.schema import MealPlanJSON

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meal_plan_2025_01.json"


def _minimal(**overrides) -> dict:
    plan = {
        "institution_name": None,
        "year_month": "2025-01",
        "days": [
            {
                "day": 2,
                "meals": [{"meal_type": "lunch", "items": [{"name": "흑미밥", "raw": "흑미밥"}]}],
            }
        ],
        "unparsed": [],
        "notes": None,
    }
    plan.update(overrides)
    return plan


def test_real_meal_plan_fixture_validates():
    data = {
        k: v
        for k, v in json.loads(FIXTURE.read_text(encoding="utf-8")).items()
        if not k.startswith("_")
    }

    plan = MealPlanJSON.model_validate(data)

    assert plan.year_month == "2025-01"
    assert len(plan.days) == 27


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


@pytest.mark.parametrize("day", [0, 32])
def test_rejects_day_outside_month(day):
    plan = _minimal()
    plan["days"][0]["day"] = day

    with pytest.raises(ValidationError, match="day"):
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
