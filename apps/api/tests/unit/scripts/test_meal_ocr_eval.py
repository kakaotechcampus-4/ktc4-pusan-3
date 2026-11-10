"""scripts/meal_ocr_eval.py — 채점 로직 (네트워크 없음)."""

import pytest

from app.core.config import settings
from app.providers.meal_plan.image_ocr import Usage
from app.providers.meal_plan.schema import MealPlanJSON
from scripts import meal_ocr_eval
from scripts.meal_ocr_eval import (
    DEFAULT_FIXTURE,
    cost_usd,
    failed_run,
    grade,
    grade_slots,
    load_fixture,
    menu_key,
    normalize_raw,
    print_c1_report,
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
    # grade_slots 는 받은 이름을 그대로 본다. 운영 이름은 원문에서 만들어 이렇게 갈리지 않지만,
    # 모델 이름 깨짐(참고)을 가를 때 같은 규칙을 쓴다 (`model_name_breaks`)
    expected = _plan(["현미밥"])
    actual = _day(("lunch", [("흑미밥", "현미밥")]))

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert slot.c1
    assert slot.wrong == ["현미밥 (이름: 흑미밥)"]


def test_name_with_junk_that_is_not_hangul_is_general_failure_not_c1():
    # 11-09 첫 측정: 닫는 괄호 자리에 " Riot" 이 붙었다. 다른 음식으로 바뀐 것이 아니다.
    # 운영은 #908 부터 이름을 원문에서 만들어 이 찌꺼기가 화면 · 저장에 가지 않는다
    expected = _plan(["백김치9(백깍두기9)"])
    actual = _day(("lunch", [("백김치(백깍두기 Riot", "백김치9(백깍두기9)")]))

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert not slot.c1
    assert slot.failed
    assert slot.garbled == ["백김치9(백깍두기9) (이름: 백김치(백깍두기 Riot)"]


def test_junk_stuck_to_the_menu_name_without_a_space_is_still_only_garbled():
    expected = _plan(["떡(증편)"])
    actual = _day(("lunch", [("떡(증편Riot", "떡(증편)")]))

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert not slot.c1
    assert slot.garbled == ["떡(증편) (이름: 떡(증편Riot)"]


def test_another_menu_in_the_name_is_c1_even_with_junk():
    expected = _plan(["현미밥"])
    actual = _day(("lunch", [("흑미밥 Riot", "현미밥")]))

    slot = grade_slots(expected, actual)[(2, "lunch")]

    assert slot.c1
    assert slot.garbled == []


def test_summary_counts_a_garbled_name_as_a_failure_but_not_as_c1():
    expected = _plan(["떡(증편)"])
    garbled = _day(("lunch", [("떡(증편 Riot", "떡(증편)")]))

    s = summarize_runs(expected, [expected, garbled])

    assert (s.c1_cases, s.total) == (0, 1)
    assert s.unstable == [(2, "lunch")]


def test_report_calls_a_garbled_name_garbled_not_missing(capsys):
    expected = _plan(["떡(증편)"])
    garbled = _day(("lunch", [("떡(증편 Riot", "떡(증편)")]))

    print_c1_report(summarize_runs(expected, [garbled]))

    line = capsys.readouterr().out.splitlines()[-1]
    assert "이름 깨짐" in line
    assert "빼먹음" not in line
    assert "떡(증편 Riot" in line


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


# ── 실행: 모델을 부르지 않는 reader 로 main 을 돈다 ─────────────────────────────

_MILLION = Usage(prompt_tokens=1_000_000, completion_tokens=0, cached_prompt_tokens=0)


def _run_main(
    monkeypatch, tmp_path, reads: list[bool], *extra: str, answer: MealPlanJSON | None = None
):
    """`reads` 순서대로 읽기에 성공(True) · 실패(False)하는 reader 로 main 을 돈다.

    회차마다 입력 100만 토큰을 쓴 것으로 친다 — 잘린 응답도 과금된다(`image_ocr._call`).
    읽기에 성공하면 `answer`(기본: 정답셋)를 모델 출력으로 낸다.
    돌려주는 것: (종료 코드, 실행 메타를 쓴 횟수, 만든 reader 수).
    """
    answer = answer or load_fixture(DEFAULT_FIXTURE)
    script = iter(reads)
    made, wrote = [], []

    class FakeReader:
        def __init__(self, *args, **kwargs):
            made.append(self)
            self.calls = 0
            self.total_usage = Usage(0, 0, 0)

        def read(self, image_bytes, *, mime_type):
            self.calls, self.total_usage = 2, _MILLION
            if not next(script):
                raise RuntimeError("응답이 출력 상한에서 잘림")
            return answer

    image = tmp_path / "meal.jpg"
    image.write_bytes(b"jpg")
    monkeypatch.setattr(settings, "MEAL_OCR_API_KEY", "test-key")
    monkeypatch.setattr(meal_ocr_eval, "ImageOcrMealPlanReader", FakeReader)
    monkeypatch.setattr(meal_ocr_eval, "_write_meta", lambda args: wrote.append(args))
    argv = [str(image), "--base-url", "http://mlapi.test", "--model", "gemini-4-argon"]
    argv += ["--out", str(tmp_path), "--repeat", str(len(reads)), *extra]
    return meal_ocr_eval.main(argv), len(wrote), len(made)


def test_repeat_beyond_the_call_budget_is_refused_before_any_call(monkeypatch, tmp_path):
    # 한 번에 호출 2회 × 50회 = 100회. 예산을 넘기면 팀 키가 삭제된다 (image_ocr.py 머리말)
    made = []
    monkeypatch.setattr(meal_ocr_eval, "ImageOcrMealPlanReader", lambda *a, **k: made.append(1))

    with pytest.raises(SystemExit):
        meal_ocr_eval.main([str(tmp_path / "meal.jpg"), "--repeat", "50"])

    assert made == []


def test_call_budget_can_be_raised_on_purpose(monkeypatch, tmp_path):
    code, _, made = _run_main(monkeypatch, tmp_path, [True] * 11, "--max-calls", "22")

    assert (code, made) == (0, 11)


def test_when_every_read_fails_nothing_is_recorded(monkeypatch, tmp_path):
    # 잰 것이 없는데 "C1 0/69" 와 실행 메타가 남으면 측정 이력에 옮겨 적힌다
    code, wrote, _ = _run_main(monkeypatch, tmp_path, [False, False])

    assert code == 1
    assert wrote == 0


def test_a_failed_read_still_costs_and_does_not_hide_the_reference_report(
    monkeypatch, tmp_path, capsys
):
    code, wrote, _ = _run_main(monkeypatch, tmp_path, [True, False])

    out = capsys.readouterr().out
    assert (code, wrote) == (0, 1)
    assert "추정 합계 $4.000" in out  # 회차마다 $2 — 실패한 회차도 과금됐다
    assert "읽기 실패 1/2회" in out
    assert "메뉴 단위 알레르기 번호 일치 : 178/178" in out  # 참고 지표는 읽힌 실행으로 낸다


# ── 운영과 같은 이름으로 채점한다 (#908) ─────────────────────────────────────────
# 운영은 모델의 name 을 쓰지 않고 원문에서 이름을 만든다(`notice_ocr.names_from_raw`).
# 모델 name 이 깨진 횟수는 프롬프트 변화를 보려고 참고로만 센다.


def _garbled_answer() -> MealPlanJSON:
    """정답셋에서 2일 점심 · 3일 오후간식 첫 항목의 모델 이름만 깨뜨린 출력 (원문은 그대로)."""
    plan = load_fixture(DEFAULT_FIXTURE).model_copy(deep=True)
    for day in plan.days:
        for meal in day.meals:
            if (day.day, meal.meal_type.value) in {(2, "lunch"), (3, "snack_pm")}:
                meal.items[0].name += " Riot"
    return plan


def test_served_names_are_graded_not_the_model_names(monkeypatch, tmp_path, capsys):
    code, _, _ = _run_main(monkeypatch, tmp_path, [True], answer=_garbled_answer())

    out = capsys.readouterr().out
    assert code == 0
    assert "C1 (정답에 없는 메뉴가 그 끼니로 읽힘) : 0/69" in out
    assert "안정 오답 0 · 불안정 0" in out  # 운영 이름에는 찌꺼기가 없다
    assert "모델 이름 깨짐(참고) : 2항목 · 회차별 [2]" in out


def test_model_name_breaks_count_junk_and_other_menu_words():
    plan = _day(
        (
            "lunch",
            [
                ("떡(증편 Riot", "떡(증편)"),
                ("흑미밥", "현미밥"),
                ('떡(꿀설기)}{"', "떡(꿀설기)"),  # 11-11 측정 M3 — 기호 찌꺼기도 화면 제목에 갔다
                ("빵(식빵)&잼", "빵(식빵2,5,6&쨈)"),  # 정답셋 식 이름 · 잼/쨈 은 깨짐이 아니다
                "우유2",
            ],
        )
    )

    assert meal_ocr_eval.model_name_breaks(plan) == [
        "떡(증편) (이름: 떡(증편 Riot)",
        "현미밥 (이름: 흑미밥) · 다른 음식 낱말",
        '떡(꿀설기) (이름: 떡(꿀설기)}{")',
    ]
