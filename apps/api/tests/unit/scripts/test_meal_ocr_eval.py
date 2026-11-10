"""scripts/meal_ocr_eval.py — 채점 로직 (네트워크 없음)."""

import pytest

from app.providers.meal_plan.image_ocr import Usage
from app.providers.meal_plan.schema import MealPlanJSON
from scripts.meal_ocr_eval import (
    DEFAULT_FIXTURE,
    cost_usd,
    failed_run,
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


def test_menu_key_ignores_trailing_separator_from_the_cell():
    # 11-09 첫 측정: 칸의 쉼표가 메뉴 끝에 붙어 나온 것이 10끼니
    assert menu_key("누룽지죽,") == menu_key("누룽지죽")
    assert menu_key("찐만두1,2,5,6,10,15,16,18,") == menu_key("찐만두1,2,5,6,10,15,16,18")
    assert menu_key("빵(프렌치토스트1,2,5,6),") == menu_key("빵(프렌치토스트)1,2,5,6")


def test_menu_key_treats_swapped_alternative_as_same_menu():
    assert menu_key("백깍두기9(백김치9)") == menu_key("백김치9(백깍두기9)")


def test_menu_key_treats_listed_spelling_variants_as_same_menu():
    assert menu_key("신선과일(샤인머스캣)") == menu_key("신선과일(샤인머스켓)")
    assert menu_key("물만둣국1,2") == menu_key("물만두국1,2")


def test_menu_key_keeps_one_letter_differences_apart():
    # 한 글자만 달라도 다른 메뉴다 — 그래서 글자 거리로 묶지 않고 표기 목록만 쓴다
    assert menu_key("현미밥") != menu_key("흑미밥")
    assert menu_key("빵(우유식빵)1,2,5,6") != menu_key("빵(우유카스텔라1,2,5,6)")


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


def test_two_menus_merged_into_one_item_is_not_c1():
    # 정답셋은 "…,우유2" 를 항목 2개로 나눈다. 모델이 한 항목으로 붙여도 메뉴는 맞다
    expected = _plan(["카레라이스5,6", "우유2"])
    actual = _plan(["카레라이스5,6,우유2"])

    assert not grade_slots(expected, actual)[(2, "lunch")].failed


def test_alternative_split_into_two_items_is_not_c1():
    expected = _plan(["경기도과일 또는 고구마우유죽2"])
    actual = _plan(["경기도과일", "고구마우유죽2"])

    assert not grade_slots(expected, actual)[(2, "lunch")].failed


def test_wrong_menu_hidden_in_a_merged_item_is_still_c1():
    expected = _plan(["카레라이스5,6", "우유2"])
    actual = _plan(["하이라이스5,6,우유2"])

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert slot.c1
    assert slot.wrong == ["하이라이스5,6,우유2"]


def test_a_failed_read_counts_as_missing_every_menu_not_c1():
    # 읽기에 실패하면 아무것도 저장되지 않는다 — 틀린 메뉴가 붙지 않으니 일반 실패
    expected = _plan_days({2: ["현미밥"], 3: ["백미밥"]})

    s = summarize_runs(expected, [expected, failed_run(expected)])

    assert s.c1_cases == 0
    assert s.unstable == [(2, "lunch"), (3, "lunch")]


# ── 운영이 실제로 싣는 것을 잰다 (photo_run._meal_entries) ──────────────────────


def _day(*meals: tuple[str, list[str | tuple[str, str]]], day: int = 2) -> MealPlanJSON:
    """끼니를 골라 만든 하루. 항목은 원문 하나, 또는 (이름, 원문) — 이름을 따로 줄 때."""

    def item(entry: str | tuple[str, str]) -> dict:
        name, raw = (entry, entry) if isinstance(entry, str) else entry
        return {"name": name, "raw": raw}

    return MealPlanJSON.model_validate(
        {
            "source": "image",
            "year_month": "2025-01",
            "days": [
                {
                    "day": day,
                    "meals": [
                        {"meal_type": meal_type, "items": [item(entry) for entry in items]}
                        for meal_type, items in meals
                    ],
                }
            ],
        }
    )


def test_name_that_disagrees_with_its_raw_is_c1():
    # 화면 제목과 저장 키는 name 으로 만든다 — raw 만 맞아서는 틀린 메뉴가 저장된다
    expected = _plan(["현미밥"])
    actual = _day(("lunch", [("흑미밥", "현미밥")]))

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert slot.c1
    assert slot.wrong == ["현미밥 (이름: 흑미밥)"]


def test_name_written_as_a_part_of_its_raw_is_not_a_failure():
    # 이름은 번호 · 분량 · 대체 메뉴를 뺀 것이다 — 정답셋도 이렇게 적는다
    items = [
        ("백김치", "백김치9(백깍두기9)"),
        ("백깍두기(백김치)", "백김치9(백깍두기9)"),
        ("고구마우유죽", "경기도과일 또는 고구마우유죽2"),
        ("빵(식빵)&잼", "빵(식빵2,5,6&쨈)"),
        ("백미밥", "백미밥1/2"),
    ]
    expected = _plan([raw for _, raw in items])

    assert not grade_slots(expected, _day(("lunch", items)))[(2, "lunch")].failed


def test_answer_set_graded_against_itself_has_no_failures():
    answer = load_fixture(DEFAULT_FIXTURE)

    assert [key for key, g in grade_slots(answer, answer).items() if g.failed] == []


def test_same_meal_listed_twice_on_a_day_is_graded_together():
    # 스키마는 같은 날짜가 두 번 나오는 것만 거절한다. 운영은 두 끼니를 다 화면에 싣는다
    expected = _plan(["찐단호박"])
    actual = _day(("lunch", ["우엉조림"]), ("lunch", ["찐단호박"]))

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert slot.c1
    assert slot.wrong == ["우엉조림"]


def test_meal_the_service_does_not_keep_is_not_graded():
    # 운영은 점심 · 오전간식 · 오후간식만 싣는다. 저녁으로 간 메뉴는 저장되지 않는다 —
    # 틀린 메뉴가 남지 않으니 C1 이 아니고, 제 끼니(오후간식)에서 빼먹은 것으로 잡힌다
    expected = _day(("snack_pm", ["찐단호박"]))
    actual = _day(("dinner", ["찐단호박"]))

    slots = grade_slots(expected, actual)

    assert list(slots) == [(2, "snack_pm")]
    assert slots[(2, "snack_pm")].failed
    assert not slots[(2, "snack_pm")].c1


def test_meal_with_numbers_but_no_menu_is_not_counted():
    # 메뉴 이름이 없는 끼니는 실패가 아닌데 분모만 늘린다
    expected = _plan(["현미밥"])
    actual = _day(("lunch", ["현미밥"]), ("snack_pm", ["1,2"]))

    assert list(grade_slots(expected, actual)) == [(2, "lunch")]


def test_report_keeps_the_c1_grading_when_a_later_run_only_fails():
    # 출력 줄에 무엇이 틀렸는지 남아야 측정 이력 §3 에 옮겨 적는다
    expected = _plan(["찐단호박"])

    s = summarize_runs(expected, [_plan(["우엉조림"]), failed_run(expected)])

    assert s.last[(2, "lunch")].wrong == ["우엉조림"]
