"""이유식 식단 표 — 모양 · 월령 범위 · 알레르기 코드 · 근거 (#399)."""

import pytest

from app.agents.common.age import first_month_of
from app.agents.common.food_safety import filter_food_safety, menu_codes
from app.agents.common.reference_docs import doc_row_id
from app.agents.food.doc_seed import food_doc_seed
from app.agents.food.weaning_menu import (
    WEANING_MENU_SOURCE,
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
    [(4, "puree"), (6, "puree"), (7, "mashed"), (8, "mashed"), (9, "minced"), (11, "minced")],
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
    row = weaning_menu_rows(8, synced_at=NOW)[0]
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
