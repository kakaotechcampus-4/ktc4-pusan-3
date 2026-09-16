"""scripts/meal_ocr_eval.py — 채점 로직 (네트워크 없음)."""

import pytest

from app.providers.meal_plan.image_ocr import Usage
from app.providers.meal_plan.schema import MealPlanJSON
from scripts.meal_ocr_eval import DEFAULT_FIXTURE, cost_usd, grade, load_fixture, normalize_raw


def _plan(items: list[str]) -> MealPlanJSON:
    return MealPlanJSON.model_validate(
        {
            "source": "image",
            "year_month": "2025-01",
            "days": [
                {
                    "day": 2,
                    "meals": [
                        {"meal_type": "lunch", "items": [{"name": r, "raw": r} for r in items]}
                    ],
                }
            ],
        }
    )


def test_normalize_raw_ignores_spacing_star_parens_and_jam_spelling():
    assert normalize_raw("연근조림★5,6") == normalize_raw("연근조림 5,6")
    assert normalize_raw("빵(핑거브레드)1,2,5,6") == normalize_raw("빵(핑거브레드1,2,5,6)")
    assert normalize_raw("빵(식빵2,5,6&쨈)") == normalize_raw("빵(식빵2,5,6&잼)")
    assert normalize_raw("호박나물5") != normalize_raw("호박나물")


def test_grade_reports_strict_normalized_and_item_level_codes():
    expected = _plan(["모듬버섯된장국5,6", "호박나물5", "연근조림★5,6"])
    actual = _plan(["모듬버섯된장국 5,6", "호박나물", "연근조림5,6"])

    g = grade(expected, actual)

    assert g["raw_exact"] == (0, 3)
    assert g["raw_normalized"] == (2, 3)  # 호박나물5 vs 호박나물 만 다름
    assert g["item_codes"] == (2, 3)  # 호박나물의 5 가 빠진 것이 메뉴 단위에서 잡힘
    assert g["meal_codes_exact"] == (1, 1)  # 끼니 합집합으로는 안 잡힘 — 그래서 메뉴 단위가 필요


def test_cost_estimate_uses_model_price_and_cache_discount():
    usage = Usage(prompt_tokens=2_000, completion_tokens=4_000, cached_prompt_tokens=1_000)

    expected = (1_000 * 5 + 1_000 * 5 / 10 + 4_000 * 25) / 1e6
    assert cost_usd(usage, "claude-opus-5") == pytest.approx(expected)
    assert cost_usd(usage, "some-unknown-model") is None


def test_answer_set_loads_as_image_plan():
    plan = load_fixture(DEFAULT_FIXTURE)

    assert plan.source.value == "image"
    assert plan.year_month == "2025-01"
    assert len(plan.days) == 27
    assert sum(len(m.items) for d in plan.days for m in d.meals) == 178
