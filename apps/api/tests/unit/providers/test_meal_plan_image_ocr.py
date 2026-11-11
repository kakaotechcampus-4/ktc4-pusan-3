"""app/providers/meal_plan/image_ocr.py — 네트워크 없이 검사할 수 있는 부분만.

실제 모델 호출은 정답셋 PR(5/5) 의 평가 스크립트로 확인한다.
"""

import base64

import pytest

from app.core.config import Settings
from app.providers.meal_plan import MealPlanSource
from app.providers.meal_plan.image_ocr import (
    RESPONSE_FORMAT,
    SYSTEM_PROMPT,
    ImageOcrMealPlanReader,
    OcrOutput,
    Usage,
    build_messages,
    merge_outputs,
    parse_output,
)

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
DEAD_END = "http://127.0.0.1:9/v1"  # 연결되지 않는 주소. 네트워크 전에 막히는 것만 검사한다


def _output(**overrides) -> OcrOutput:
    data = {
        "institution_name": None,
        "year_month": "2025-01",
        "days": [],
        "unparsed": [],
        "notes": None,
    }
    data.update(overrides)
    return OcrOutput.model_validate(data)


def test_build_messages_embeds_image_as_data_uri():
    messages = build_messages(PNG_HEADER, "image/png")

    assert messages[0]["role"] == "system"
    image_part = next(p for p in messages[1]["content"] if p["type"] == "image_url")
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_HEADER


def test_build_messages_can_restrict_day_range():
    messages = build_messages(PNG_HEADER, "image/png", day_range=(16, 31))

    text = next(p for p in messages[1]["content"] if p["type"] == "text")["text"]
    assert "16일" in text and "31일" in text


def test_build_messages_rejects_unsupported_mime():
    with pytest.raises(ValueError, match="형식"):
        build_messages(PNG_HEADER, "application/pdf")


def test_system_prompt_keeps_design_decisions():
    assert "allergen_codes" in SYSTEM_PROMPT  # 만들지 말라고 명시
    assert "unparsed" in SYSTEM_PROMPT  # 못 읽으면 지어내지 말고 여기로
    assert "raw" in SYSTEM_PROMPT  # 원문 보존
    assert '"dinner"' in SYSTEM_PROMPT  # 석식형 급식표


def test_model_schema_is_strict_and_has_no_source():
    schema = RESPONSE_FORMAT["json_schema"]["schema"]

    assert RESPONSE_FORMAT["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "institution_name",
        "year_month",
        "days",
        "unparsed",
        "notes",
    }
    assert "source" not in schema["properties"]  # 출처는 읽기 코드가 채운다


def test_usage_adds_token_counts():
    total = Usage(prompt_tokens=2_000, completion_tokens=4_000) + Usage(10, 20, 5)

    assert (total.prompt_tokens, total.completion_tokens, total.cached_prompt_tokens) == (
        2_010,
        4_020,
        5,
    )


# ── 게이트웨이가 응답을 스키마 이름으로 한 겹 감싸는 경우가 있다 (실측 2026-09-09) ──

_PLAN = (
    '{"institution_name": null, "year_month": "2025-01", "days": [], "unparsed": [], "notes": null}'
)


@pytest.mark.parametrize("content", [_PLAN, '{"MealPlanJSON": ' + _PLAN + "}"])
def test_parse_output_accepts_wrapped_and_unwrapped(content):
    assert parse_output(content).year_month == "2025-01"


def test_parse_output_reports_bad_json_and_bad_shape_as_runtime_error():
    with pytest.raises(RuntimeError, match="JSON"):
        parse_output("not json")
    with pytest.raises(RuntimeError, match="파싱"):
        parse_output('{"MealPlanJSON": {"days": "not a list"}}')


def test_parse_output_rejects_source_from_the_model():
    with pytest.raises(RuntimeError, match="파싱"):
        parse_output('{"source": "image", ' + _PLAN[1:])


# ── 게이트웨이 출력 상한(6,000 토큰) 때문에 날짜 범위를 나눠 부르고 합친다 ──


def test_merge_outputs_sets_source_and_orders_days():
    first = _output(
        institution_name="(가림)",
        days=[{"day": 2, "meals": []}, {"day": 1, "meals": [], "note": "휴일"}],
        unparsed=[{"why": "앞쪽 흐림"}],
        notes="안내 A",
    )
    second = _output(
        days=[{"day": 16, "meals": []}], unparsed=[{"why": "뒤쪽 흐림"}], notes="안내 B"
    )

    merged = merge_outputs([first, second])

    assert merged.source is MealPlanSource.IMAGE
    assert [d.day for d in merged.days] == [1, 2, 16]
    assert merged.institution_name == "(가림)"
    assert [u.why for u in merged.unparsed] == ["앞쪽 흐림", "뒤쪽 흐림"]
    assert merged.notes == "안내 A / 안내 B"


def _lunch(day: int, *raws: str) -> dict:
    return {
        "day": day,
        "meals": [{"meal_type": "lunch", "items": [{"name": r, "raw": r} for r in raws]}],
    }


def test_merge_outputs_drops_duplicate_day_from_later_call_and_flags_it():
    first = _output(days=[{"day": 15, "meals": [], "note": "앞"}])
    second = _output(days=[{"day": 15, "meals": [], "note": "뒤"}])

    merged = merge_outputs([first, second])

    assert [d.note for d in merged.days] == ["앞"]
    assert merged.unparsed[0].day == 15


def test_merge_outputs_keeps_what_the_dropped_duplicate_day_read():
    # 버린 쪽에 무엇이 있었는지 보호자가 볼 수 있어야 한다 — 확인 화면의 제목이 이 원문이다
    first = _output(days=[_lunch(15, "흑미밥")])
    second = _output(days=[_lunch(15, "현미밥", "미역국5,6")])

    merged = merge_outputs([first, second])

    assert [[i.raw for m in d.meals for i in m.items] for d in merged.days] == [["흑미밥"]]
    (cell,) = merged.unparsed
    assert (cell.day, cell.meal_type, cell.raw) == (15, None, "현미밥, 미역국5,6")
    assert "두 번" in cell.why


def test_merge_outputs_keeps_one_of_the_same_unread_cell_reported_by_both_calls():
    # 날짜 없는 열(생일식단)은 1~15일 · 16~31일 두 호출이 다 낸다 — 확인 화면에 같은 칸이 두 번
    # 뜨면 안 된다 (11-11 측정 M2: 3칸이 6줄). 이유 문구는 호출마다 달라 원문으로 같은 칸을 가린다
    birthday = [
        ("snack_am", "경기도과일 또는 단호박죽"),
        ("lunch", "백미밥, 북어채미역국5,6, 치킨가스&소스1,5,6,15"),
        ("snack_pm", "케이크1,2,5,6(떡),우유2"),
    ]
    first = _output(
        unparsed=[{"meal_type": m, "raw": r, "why": "생일식단 (날짜 없음)"} for m, r in birthday]
    )
    second = _output(
        unparsed=[{"meal_type": m, "raw": r, "why": "날짜 없는 열"} for m, r in birthday]
    )

    merged = merge_outputs([first, second])

    assert [(u.meal_type, u.raw, u.why) for u in merged.unparsed] == [
        (m, r, "생일식단 (날짜 없음)") for m, r in birthday
    ]


def test_merge_outputs_keeps_unread_cells_without_raw_even_if_alike():
    # 원문이 없으면 같은 칸인지 알 수 없다 — 지우지 않는다
    blurry = [{"why": "흐림"}]
    merged = merge_outputs([_output(unparsed=blurry), _output(unparsed=blurry)])

    assert len(merged.unparsed) == 2


def test_merge_outputs_sets_aside_only_the_day_the_month_does_not_have():
    # 전에는 그 하루 때문에 한 달 표 전체가 "식단표의 표를 읽지 못했어요" 였다.
    # 2월 30일은 날짜를 잘못 읽은 것이라 저장하지 않고 못 읽은 칸으로 확인을 받는다
    merged = merge_outputs(
        [_output(days=[_lunch(27, "흑미밥"), _lunch(30, "현미밥", "우유2")])],
        year_month="2025-02",
    )

    assert [d.day for d in merged.days] == [27]
    (cell,) = merged.unparsed
    assert (cell.day, cell.raw) == (None, "현미밥, 우유2")  # 없는 날이라 날짜 칸에 싣지 않는다
    assert "30일" in cell.why and "2월" in cell.why


def test_merge_outputs_still_rejects_a_year_month_it_cannot_use():
    # 월을 모르면 날짜를 하나도 쓸 수 없다 — 이때만 표 전체가 실패다
    with pytest.raises(RuntimeError, match="규칙"):
        merge_outputs([_output(year_month="11", days=[_lunch(2, "흑미밥")])])


# ── 예산 보호: 네트워크 전에 막힌다 ──


def test_read_refuses_up_front_when_ranges_exceed_call_cap():
    reader = ImageOcrMealPlanReader(base_url=DEAD_END, api_key="x", max_calls=1)

    with pytest.raises(RuntimeError, match="상한"):
        reader.read(PNG_HEADER, mime_type="image/png")  # 기본 2회 필요, 상한 1회


def test_read_rejects_unsupported_mime_before_any_call():
    reader = ImageOcrMealPlanReader(base_url=DEAD_END, api_key="x", max_calls=0)

    with pytest.raises(ValueError, match="형식"):
        reader.read(b"", mime_type="application/pdf")
    assert reader.calls == 0


def test_from_settings_requires_url_and_key():
    settings = Settings(
        DB_HOST="h",
        DB_USER="u",
        DB_PASSWORD="p",
        DB_NAME="n",
        AUTH_RETURN_URL_WEB="http://localhost:3000/auth/callback",
        AUTH_RETURN_URL_APP="icatch://auth",
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="MEAL_OCR"):
        ImageOcrMealPlanReader.from_settings(settings)

    reader = ImageOcrMealPlanReader.from_settings(
        settings.model_copy(update={"MEAL_OCR_BASE_URL": DEAD_END, "MEAL_OCR_API_KEY": "x"}),
        max_calls=0,
    )
    assert reader.source is MealPlanSource.IMAGE
    assert reader.model == settings.MEAL_OCR_MODEL


# ── 한 날짜 칸이 틀려도 그 호출 전체를 버리지 않는다 ──


@pytest.mark.parametrize("bad_day", [0, 32, 45])
def test_parse_output_sets_aside_a_day_out_of_range_instead_of_failing_the_call(bad_day):
    # 응답 스키마는 day 를 정수로만 묶는다 — 1~31 밖의 날은 검증에서 걸려, 전에는 그 호출의
    # 15일치가 다 버려졌다. 그 날만 못 읽은 칸으로 돌린다
    import json

    content = json.dumps(
        {
            "institution_name": None,
            "year_month": "11",
            "days": [_lunch(2, "흑미밥"), _lunch(bad_day, "현미밥", "우유2")],
            "unparsed": [{"day": 3, "meal_type": "lunch", "raw": "빵(찐", "why": "잘림"}],
            "notes": None,
        },
        ensure_ascii=False,
    )

    out = parse_output(content)

    assert [d.day for d in out.days] == [2]
    assert [(u.day, u.raw) for u in out.unparsed] == [(3, "빵(찐"), (None, "현미밥, 우유2")]
    assert f"{bad_day}일" in out.unparsed[1].why
