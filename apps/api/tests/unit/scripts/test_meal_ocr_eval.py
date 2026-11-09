"""scripts/meal_ocr_eval.py — 채점 로직 (네트워크 없음)."""

import pytest

from app.providers.meal_plan.image_ocr import Usage
from app.providers.meal_plan.schema import MealPlanJSON
from scripts.meal_ocr_eval import (
    DEFAULT_FIXTURE,
    cost_usd,
    grade,
    grade_slots,
    load_fixture,
    menu_key,
    normalize_raw,
    summarize_runs,
)


def _plan(items: list[str], *, day: int = 2) -> MealPlanJSON:
    return _plan_days({day: items})


def _plan_days(days: dict[int, list[str]]) -> MealPlanJSON:
    return MealPlanJSON.model_validate(
        {
            "source": "image",
            "year_month": "2025-01",
            "days": [
                {
                    "day": day,
                    "meals": [
                        {"meal_type": "lunch", "items": [{"name": r, "raw": r} for r in items]}
                    ],
                }
                for day, items in days.items()
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


# ── C1 (11-09 PM): 정답과 다른 메뉴가 그 날짜 · 끼니의 급식으로 읽힘 ──────────────


def test_menu_key_ignores_allergen_numbers_and_spacing():
    assert menu_key("연근조림★5,6") == menu_key("연근조림 5.6")
    assert menu_key("백김치9(백깍두기9)") == menu_key("백김치 (백깍두기)")
    assert menu_key("연근조림5,6") != menu_key("우엉조림5,6")


def test_wrong_menu_in_a_meal_is_c1():
    expected = _plan(["현미밥", "연근조림5,6"])
    actual = _plan(["현미밥", "우엉조림5,6"])  # 연근조림을 우엉조림으로 읽음

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert slot.c1
    assert slot.wrong == ["우엉조림5,6"]
    assert slot.missing == ["연근조림5,6"]


def test_only_missing_menu_is_general_failure_not_c1():
    expected = _plan(["현미밥", "연근조림5,6"])
    actual = _plan(["현미밥"])  # 빼먹기만 함 — 틀린 메뉴가 기록에 붙지 않는다

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert not slot.c1
    assert slot.failed
    assert slot.missing == ["연근조림5,6"]


def test_allergen_number_or_spacing_difference_is_not_a_failure():
    expected = _plan(["모듬버섯된장국5,6", "호박나물5"])
    actual = _plan(["모듬버섯된장국 5,6", "호박나물"])  # 번호는 서비스가 읽지 않는다 (10-09)

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert not slot.c1
    assert not slot.failed


def test_menus_on_a_day_without_meals_are_c1():
    expected = _plan_days({2: ["현미밥"]})
    actual = _plan_days({1: ["현미밥"], 2: ["현미밥"]})  # 휴일(1일)에 급식이 생김 — 날짜 밀림

    slots = grade_slots(expected, actual)

    assert slots[(1, "lunch")].c1
    assert not slots[(2, "lunch")].failed


def test_summary_counts_c1_cases_stable_and_unstable_over_repeats():
    expected = _plan_days({2: ["현미밥", "연근조림"], 3: ["백미밥"], 6: ["흑미밥", "된장국"]})
    runs = [
        # 2일: 매번 틀린 메뉴(C1 · 안정 오답) / 3일: 한 번 빼먹음(불안정) / 6일: 늘 맞음
        _plan_days({2: ["현미밥", "우엉조림"], 3: ["백미밥"], 6: ["흑미밥", "된장국"]}),
        _plan_days({2: ["현미밥", "우엉조림"], 3: [], 6: ["흑미밥", "된장국"]}),
        _plan_days({2: ["현미밥", "우엉조림"], 3: ["백미밥"], 6: ["흑미밥", "된장국"]}),
    ]

    s = summarize_runs(expected, runs)

    assert (s.c1_cases, s.total) == (1, 3)
    assert s.stable_wrong == [(2, "lunch")]
    assert s.unstable == [(3, "lunch")]
    assert s.c1_runs[(2, "lunch")] == 3


def test_summary_counts_an_extra_meal_once_even_if_it_appears_in_one_run():
    expected = _plan_days({2: ["현미밥"]})
    runs = [_plan_days({2: ["현미밥"]}), _plan_days({1: ["현미밥"], 2: ["현미밥"]})]

    s = summarize_runs(expected, runs)

    assert (s.c1_cases, s.total) == (1, 2)  # 정답 끼니 1 + 정답에 없는데 생긴 끼니 1
    assert s.unstable == [(1, "lunch")]


def test_answer_set_has_69_meals():
    assert len(grade_slots(load_fixture(DEFAULT_FIXTURE), load_fixture(DEFAULT_FIXTURE))) == 69
