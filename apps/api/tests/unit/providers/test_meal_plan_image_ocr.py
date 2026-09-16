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


def test_merge_outputs_drops_duplicate_day_from_later_call_and_flags_it():
    first = _output(days=[{"day": 15, "meals": [], "note": "앞"}])
    second = _output(days=[{"day": 15, "meals": [], "note": "뒤"}])

    merged = merge_outputs([first, second])

    assert [d.note for d in merged.days] == ["앞"]
    assert merged.unparsed[0].day == 15


def test_merge_outputs_rejects_day_the_month_does_not_have():
    with pytest.raises(RuntimeError, match="규칙"):
        merge_outputs([_output(year_month="2025-02", days=[{"day": 30, "meals": []}])])


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
