"""app/providers/meal_ocr/mlapi.py — 네트워크 없이 검사할 수 있는 부분만."""

import base64

import pytest
from openai.lib._pydantic import to_strict_json_schema

from app.providers.meal_ocr.mlapi import (
    SYSTEM_PROMPT,
    MlapiMealOcr,
    Usage,
    build_messages,
    merge_plans,
    parse_plan_content,
)
from app.providers.meal_ocr.schema import MealPlanJSON

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_build_messages_embeds_image_as_data_uri():
    messages = build_messages(PNG_HEADER, "image/png")

    assert messages[0]["role"] == "system"
    user = messages[1]
    assert user["role"] == "user"
    image_part = next(p for p in user["content"] if p["type"] == "image_url")
    url = image_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_HEADER


def test_system_prompt_keeps_design_decisions():
    assert "allergen_codes" in SYSTEM_PROMPT  # 만들지 말라고 명시
    assert "unparsed" in SYSTEM_PROMPT  # 못 읽으면 지어내지 말고 여기로
    assert "raw" in SYSTEM_PROMPT  # 원문 보존


def test_schema_converts_to_strict_json_schema():
    schema = to_strict_json_schema(MealPlanJSON)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "institution_name",
        "year_month",
        "days",
        "unparsed",
        "notes",
    }


def test_usage_cost_estimate_uses_model_price():
    usage = Usage(prompt_tokens=2_000, completion_tokens=4_000, cached_prompt_tokens=0)

    assert usage.cost_usd("claude-opus-5") == pytest.approx(2_000 / 1e6 * 5 + 4_000 / 1e6 * 25)
    assert usage.cost_usd("some-unknown-model") is None


def test_call_cap_blocks_before_any_network_call():
    ocr = MlapiMealOcr(base_url="http://127.0.0.1:9/v1", api_key="x", max_calls=0)

    with pytest.raises(RuntimeError, match="상한"):
        ocr.extract(PNG_HEADER, "image/png")


# ── 게이트웨이 출력 상한(6,000 토큰) 때문에 날짜 범위를 나눠 부른다 ──────────────


def test_build_messages_can_restrict_day_range():
    messages = build_messages(PNG_HEADER, "image/png", day_range=(16, 31))

    text = next(p for p in messages[1]["content"] if p["type"] == "text")["text"]
    assert "16일" in text and "31일" in text


def test_merge_plans_concatenates_days_in_order_and_keeps_unparsed():
    first = MealPlanJSON.model_validate(
        {
            "institution_name": "(가림)",
            "year_month": "2025-01",
            "days": [{"day": 2, "meals": []}, {"day": 1, "meals": [], "note": "휴일"}],
            "unparsed": [{"why": "앞쪽 흐림"}],
            "notes": "안내 A",
        }
    )
    second = MealPlanJSON.model_validate(
        {
            "institution_name": None,
            "year_month": "2025-01",
            "days": [{"day": 16, "meals": []}],
            "unparsed": [{"why": "뒤쪽 흐림"}],
            "notes": "안내 B",
        }
    )

    merged = merge_plans([first, second])

    assert [d.day for d in merged.days] == [1, 2, 16]
    assert merged.institution_name == "(가림)"
    assert [u.why for u in merged.unparsed] == ["앞쪽 흐림", "뒤쪽 흐림"]
    assert merged.notes == "안내 A / 안내 B"


def test_merge_plans_drops_duplicate_day_from_later_call_and_flags_it():
    base = {"institution_name": None, "year_month": "2025-01", "unparsed": [], "notes": None}
    first = MealPlanJSON.model_validate({**base, "days": [{"day": 15, "meals": [], "note": "앞"}]})
    second = MealPlanJSON.model_validate({**base, "days": [{"day": 15, "meals": [], "note": "뒤"}]})

    merged = merge_plans([first, second])

    assert [d.note for d in merged.days] == ["앞"]
    assert merged.unparsed[0].day == 15


def test_extract_refuses_up_front_when_ranges_exceed_call_cap():
    ocr = MlapiMealOcr(
        base_url="http://127.0.0.1:9/v1", api_key="x", max_calls=1, day_ranges=((1, 15), (16, 31))
    )

    with pytest.raises(RuntimeError, match="상한"):
        ocr.extract(PNG_HEADER, "image/png")  # 2회가 필요한데 상한 1회 → 네트워크 전에 거절


# ── 게이트웨이가 응답을 스키마 이름으로 한 겹 감싸는 경우가 있다 (실측 2026-09-09) ──

_PLAN = (
    '{"institution_name": null, "year_month": "2025-01", "days": [], "unparsed": [], "notes": null}'
)


@pytest.mark.parametrize("content", [_PLAN, '{"MealPlanJSON": ' + _PLAN + "}"])
def test_parse_plan_content_accepts_wrapped_and_unwrapped(content):
    plan = parse_plan_content(content)

    assert plan.year_month == "2025-01"


def test_parse_plan_content_reports_bad_json_without_crashing_on_pydantic():
    with pytest.raises(RuntimeError, match="파싱"):
        parse_plan_content('{"MealPlanJSON": {"days": "not a list"}}')
