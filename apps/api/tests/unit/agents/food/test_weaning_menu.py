"""이유식 식단 표 — 모양 · 월령 범위 · 알레르기 코드 · 근거 (#399)."""

from dataclasses import replace
from datetime import date

import pytest

from app.agents.common.age import first_month_of
from app.agents.common.food_safety import filter_food_safety, menu_codes
from app.agents.common.reference_docs import doc_row_id
from app.agents.food import weaning_menu
from app.agents.food.doc_seed import food_doc_seed
from app.agents.food.weaning_menu import (
    WEANING_MENU_SOURCE,
    parse_weaning_menu,
    weaning_menu_citation,
    weaning_menu_id,
    weaning_menu_rows,
    weaning_menu_table,
)
from tests.unit.agents.food import support

NOW = support.NOW


def test_출처는_아이캐치_서비스_일반추천이다():
    table = weaning_menu_table()
    assert (table.source_org, table.source_title) == (
        "아이캐치 서비스 일반추천",
        "월령별 이유식 추천 식단",
    )


def test_키가_겹치지_않고_food_doc_시드와도_id_가_겹치지_않는다():
    keys = [e.key for e in weaning_menu_table().entries]
    assert len(keys) == len(set(keys)) == 30
    doc_ids = {doc_row_id("food_doc", e.meta.doc_key) for e in food_doc_seed()}
    assert not {weaning_menu_id(k) for k in keys} & doc_ids


@pytest.mark.parametrize(
    ("months", "texture"),
    [
        (4, "puree"),
        (5, "puree"),
        (6, "mashed"),
        (7, "mashed"),
        (8, "minced"),
        (11, "minced"),
    ],
)
def test_월령마다_그_단계_행만_나온다(months, texture):
    rows = weaning_menu_rows(months, synced_at=NOW)
    assert len(rows) >= 5
    by_key = {e.key: e for e in weaning_menu_table().entries}
    assert {by_key[r.source_id].texture for r in rows} == {texture}


@pytest.mark.parametrize("months", [0, 3, 12, 36])
def test_이유기_밖_월령은_행이_없다(months):
    assert weaning_menu_rows(months, synced_at=NOW) == ()


def test_월령_범위는_age_gates_의_이유기와_같다():
    entries = weaning_menu_table().entries
    assert min(e.min_month for e in entries) == first_month_of("infant_weaning")
    assert max(e.max_month for e in entries) == first_month_of("toddler")


# food_doc 의 질감 행(한국소비자원 · 뉴질랜드 지침, 4–5 / 6–7 / 8–11개월)이 단계 경계의 출처다.
# 식단 표가 따로 달라지면 guide_weaning_stage 안내와 식단 추천이 한 달씩 어긋난다.
_TEXTURE_DOC_KEYS = {
    "puree": "food.weaning.stage.m4_5.texture",
    "mashed": "food.weaning.stage.m6_7.texture",
    "minced": "food.weaning.stage.m8_11.texture",
}


def test_단계_경계가_food_doc_질감_행과_같다():
    docs = {e.meta.doc_key: e.meta for e in food_doc_seed()}
    spans: dict[str, set[tuple[int, int]]] = {}
    for e in weaning_menu_table().entries:
        spans.setdefault(e.texture, set()).add((e.min_month, e.max_month))

    assert set(spans) == set(_TEXTURE_DOC_KEYS)
    for texture, doc_key in _TEXTURE_DOC_KEYS.items():
        meta = docs[doc_key]
        assert spans[texture] == {(meta.min_month, meta.max_month)}, texture


def test_알레르기_코드가_이름과_재료를_덮는다():
    for e in weaning_menu_table().entries:
        assert menu_codes([e.name, *e.ingredients]) <= e.allergen_codes, e.key


@pytest.mark.parametrize("months", range(4, 12))
def test_안전_정보가_없는_아이에게는_모든_행이_통과한다(months):
    rows = weaning_menu_rows(months, synced_at=NOW)
    verdict = filter_food_safety(rows, (), "infant_weaning", months=months)
    assert (verdict.blocked, verdict.unchecked) == ((), ())


def test_주재료에_쌀은_쌀미음만_적는다():
    for e in weaning_menu_table().entries:
        assert ("쌀" in e.main_ingredients) == (e.name == "쌀미음"), e.key


def test_행은_이유식_소스의_카탈로그_행이다():
    row = weaning_menu_rows(7, synced_at=NOW)[0]
    assert (row.source, row.stage_min, row.resolved) == (
        WEANING_MENU_SOURCE,
        "infant_weaning",
        True,
    )
    assert row.source_id.startswith("food.weaning_menu.mid.") and row.ingredients


def test_근거는_아이_기록이_아닌_food_doc_문서_행이다():
    row = weaning_menu_rows(8, synced_at=NOW)[0]
    c = weaning_menu_citation(row)
    assert (c.ref.kind, c.ref.id, c.label) == (
        "food_doc",
        weaning_menu_id(row.source_id),
        row.display_name,
    )
    assert not c.ref.is_child_record and c.note == weaning_menu_table().note


# ── 검수 상태 — 출처 기관이 없으니 사람 검수가 유일한 안전장치다 ───────────────────────────
def _raw_row(**overrides: object) -> dict[str, object]:
    return {
        "key": "food.weaning_menu.early.rice",
        "name": "쌀미음",
        "min_month": 4,
        "max_month": 6,
        "texture": "puree",
        "ingredients": ["쌀"],
        "main_ingredients": ["쌀"],
        "food_groups": ["grains"],
        "allergen_codes": [],
        "status": "approved",
        "authored_by": "nnhhlee",
        "reviewed_by": "admin",
        "reviewed_at": date(2026, 11, 10),
        **overrides,
    }


def _raw_table(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "source": "아이캐치 서비스 일반추천",
        "title": "식단",
        "note": "한 줄",
        "rows": list(rows),
    }


def test_모든_행에_작성자가_있고_approved_행에는_검수자와_검수일이_있다():
    for e in weaning_menu_table().entries:
        assert e.authored_by, e.key
        if e.status == "approved":
            assert e.reviewed_by and e.reviewed_at, e.key


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "maybe"}, "status"),
        ({"authored_by": ""}, "authored_by"),
        ({"reviewed_by": None}, "approved 인데 검수자 · 검수일이 없다"),
        ({"reviewed_at": None}, "approved 인데 검수자 · 검수일이 없다"),
        ({"reviewed_by": "nnhhlee"}, "작성자와 검수자가 같다"),
    ],
)
def test_검수_칸이_틀리면_읽을_때_실패한다(overrides, message):
    with pytest.raises(ValueError, match=message):
        parse_weaning_menu(_raw_table(_raw_row(**overrides)))


def test_draft_는_검수자_없이도_읽힌다():
    table = parse_weaning_menu(
        _raw_table(_raw_row(status="draft", reviewed_by=None, reviewed_at=None))
    )

    assert [e.status for e in table.entries] == ["draft"]


@pytest.mark.parametrize("status", ["draft", "retired"])
def test_approved_가_아닌_행은_풀에_들지_않는다(monkeypatch, status):
    table = weaning_menu_table()
    held = replace(table.entries[0], status=status, reviewed_by=None, reviewed_at=None)
    monkeypatch.setattr(
        weaning_menu,
        "weaning_menu_table",
        lambda: replace(table, entries=(held, *table.entries[1:])),
    )

    keys = {r.source_id for r in weaning_menu_rows(held.min_month, synced_at=NOW)}

    assert held.key not in keys
    assert keys == {e.key for e in table.entries[1:] if e.min_month <= held.min_month < e.max_month}
