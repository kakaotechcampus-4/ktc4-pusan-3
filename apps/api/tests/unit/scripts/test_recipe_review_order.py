"""검수 순서 — 가장 적게 검수해서 레시피를 가장 많이 푸는 순서 (FT-18)."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.agents.food.recipe_catalog import build_recipe_row, parse_ingredient_table
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync
from scripts import recipe_fetch
from scripts.recipe_review_order import build_parser, main, review_order


def table(**statuses: str):
    return parse_ingredient_table(
        {
            "source": "mafra_recipe",
            "version": "test",
            "fetched_at": "2026-10-14",
            "ingredients": [
                {"raw": raw, "names": [raw], "status": status} for raw, status in statuses.items()
            ],
        }
    )


def test_재료가_적게_남은_레시피부터_푼다():
    recipes = [["두부", "된장"], ["두부"], ["멸치", "간장", "설탕"]]
    order = review_order(recipes, table(두부="draft", 된장="draft", 멸치="draft"), target=2)

    assert order == [("두부", 1), ("된장", 2)]


def test_이미_정리된_재료는_다시_묻지_않는다():
    order = review_order([["두부", "간장"]], table(두부="ok", 간장="draft"), target=1)

    assert order == [("간장", 1)]


def test_구성을_알_수_없는_재료가_든_레시피는_풀_수_없다():
    # unknown 은 검수했지만 구성을 모른다 — 다시 봐도 풀리지 않는다
    order = review_order(
        [["육수", "두부"], ["두부"]], table(육수="unknown", 두부="draft"), target=2
    )

    assert order == [("두부", 1)]


def test_음식이_아닌_재료는_정리된_것으로_센다():
    # not_food(물 · 얼음)는 재료로 치지 않는다 — 검수할 것이 남지 않는다
    order = review_order([["물", "두부"]], table(물="not_food", 두부="draft"), target=1)

    assert order == [("두부", 1)]


def test_이름이_빈_재료가_있는_레시피는_풀_수_없다():
    assert review_order([[None, "두부"]], table(두부="draft"), target=1) == []


def test_재료가_하나도_없는_레시피는_풀_수_없다():
    # build_recipe_row 는 재료가 없으면 resolved=False 로 둔다 — 처음부터 풀린 것으로 세면 안 된다
    assert review_order([[], ["두부"]], table(두부="draft"), target=1) == [("두부", 1)]


def test_순서가_약속한_레시피가_실제로_resolved_가_된다():
    """순서의 숫자와 `build_recipe_row` 의 판정이 어긋나면 검수한 사람이 헛수고를 한다."""
    names = [["두부", "된장"], ["두부"], ["멸치", "간장"], ["멸치", "육수"], ["양념"], []]
    recipes = [
        RawRecipe(str(i), f"레시피{i}", ingredients=tuple(RawIngredient(n) for n in group))
        for i, group in enumerate(names)
    ]
    drafts = table(
        두부="draft", 된장="draft", 멸치="draft", 간장="draft", 육수="unknown", 양념="draft"
    )

    order = review_order(names, drafts, target=3)
    reviewed = {key for key, _ in order}
    after = {
        key: (replace(entry, status="ok") if key in reviewed else entry)
        for key, entry in drafts.items()
    }

    now = datetime(2026, 10, 14, tzinfo=UTC)
    resolved = sum(
        build_recipe_row(r, after, now=now, source="mafra_recipe").resolved for r in recipes
    )
    assert resolved == order[-1][1]


def test_출력은_cp949_로_못_찍는_글자가_있어도_UTF8_파일로_남는다(tmp_path):
    # `> review_order.txt` 는 Windows 에서 cp949 라 `—` 한 글자에 목록 중간에서 죽는다
    sync = {
        "recipes": [
            {"ingredients": [{"name": "두부 — 국산"}, {"name": "된장"}]},
            {"ingredients": [{"name": "두부 — 국산"}]},
        ]
    }
    source = tmp_path / "sync.json"
    source.write_text(json.dumps(sync, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "order.txt"

    code = main(
        ["--input", str(source), "--target", "2", "--output", str(out)],
        table=table(**{"두부 — 국산": "draft", "된장": "draft"}),
    )

    assert code == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["   1  두부 — 국산", "   2  된장"]


def test_도움말은_cp949_콘솔에서도_찍힌다():
    build_parser().format_help().encode("cp949")


# ── --source (#510) ───────────────────────────────────────────────────
def _sync(*names: str) -> RecipeSync:
    return RecipeSync(
        recipes=(RawRecipe("1", "가짜", ingredients=tuple(RawIngredient(n) for n in names)),),
        recipe_rows=1,
        ingredient_rows=len(names),
        orphan_ingredient_rows=0,
        unnamed_ingredient_rows=0,
    )


@pytest.fixture
def fetchers(monkeypatch):
    calls: list[str] = []

    async def fake_mafra():
        calls.append("mafra")
        return _sync("마파재료")

    async def fake_nics():
        calls.append("nics")
        return _sync("닉스재료")

    monkeypatch.setattr(recipe_fetch, "fetch_mafra", fake_mafra)
    monkeypatch.setattr(recipe_fetch, "fetch_nics", fake_nics)
    return calls


def test_source_는_정해진_이름만_받는다():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--fetch", "--source", "unknown"])


def test_source_없이_받으면_농식품정보원이다(tmp_path, fetchers):
    out = tmp_path / "order.txt"

    main(["--fetch", "--target", "1", "--output", str(out)], table=table(마파재료="draft"))

    assert fetchers == ["mafra"]
    assert out.read_text(encoding="utf-8").splitlines() == ["   1  마파재료"]


def test_받아_둔_결과에는_source_를_붙일_수_없다(tmp_path, fetchers):
    # 받아 둔 결과는 이미 소스가 정해져 있다 — 붙여도 무시되니 받지 않는다
    source = tmp_path / "sync.json"
    source.write_text(json.dumps({"recipes": [{"ingredients": [{"name": "두부"}]}]}), "utf-8")

    with pytest.raises(SystemExit):
        main(
            ["--input", str(source), "--source", "nics", "--target", "1"], table=table(두부="draft")
        )
    assert fetchers == []


def test_nics_로_받으면_NICS_클라이언트만_쓴다(tmp_path, fetchers):
    out = tmp_path / "order.txt"

    main(
        ["--fetch", "--source", "nics", "--target", "1", "--output", str(out)],
        table=table(닉스재료="draft", 마파재료="draft"),
    )

    assert fetchers == ["nics"]
    assert out.read_text(encoding="utf-8").splitlines() == ["   1  닉스재료"]
