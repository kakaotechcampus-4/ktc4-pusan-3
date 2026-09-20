"""app/rules/allergen.py — 정답셋(178항목) 회귀. #64 에서 정답셋이 올라올 때까지 미뤄 둔 두 개."""

import json
from pathlib import Path

from app.rules.allergen import parse_allergens

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meal_plan_2025_01.json"


def _fixture_raws() -> list[str]:
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [it["raw"] for d in plan["days"] for m in d["meals"] for it in m["items"]]


def test_real_meal_plan_yields_no_out_of_range_numbers():
    bad = {raw: r.unknown for raw in _fixture_raws() if (r := parse_allergens(raw)).unknown}

    assert bad == {}


def test_real_meal_plan_numbers_are_all_parsed():
    not_allergen = {"백미밥1/2"}
    missed = [
        raw
        for raw in _fixture_raws()
        if any(ch.isdigit() for ch in raw)
        and raw not in not_allergen
        and not parse_allergens(raw).codes
    ]

    assert missed == []
