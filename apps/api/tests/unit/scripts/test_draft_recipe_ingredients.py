"""scripts/draft_recipe_ingredients.py — 레시피 재료 정리 표 초안 (네트워크 없음).

초안은 사람이 검수할 후보다. 여기서 지키는 것 셋.
    ① 초안은 어떤 항목도 `ok` 로 만들지 않는다 — 검수 전에는 레시피가 후보 풀에 들어가지 않는다
    ② 사전이 모르는 이름(오타 · 부위명 · 묶음 표기)에 넓은 코드 후보를 붙인다
       — 덜 막는 쪽으로 틀리지 않게
    ③ 다시 돌려도 사람이 고친 항목은 그대로 둔다
재료 이름은 일반 식재료 낱말이고 레시피는 가짜다.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents.food.recipe_catalog import parse_ingredient_table
from app.core.config import settings
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync
from app.integrations.nics.client import NicsApiError
from scripts import recipe_fetch
from scripts.draft_recipe_ingredients import (
    EXTRA_CODES,
    build_draft,
    build_parser,
    draft_entry,
    main,
    merge_entries,
    render_yaml,
)


def sync(*recipes: list[str | None]) -> dict[str, Any]:
    """RecipeSync 를 JSON 으로 저장한 모양."""
    return {
        "recipes": [
            {
                "recipe_id": str(i),
                "name": f"가짜{i}",
                "nation": None,
                "kind": None,
                "ingredients": [{"name": n, "amount": None, "role": None} for n in names],
            }
            for i, names in enumerate(recipes, 1)
        ],
    }


def table_of(entries: list[dict[str, Any]]) -> dict:
    return parse_ingredient_table(
        {"source": "t", "version": "t", "fetched_at": "2026-10-11", "ingredients": entries}
    )


# ── ① 초안은 검수 전이다 ──────────────────────────────────────────────
def test_초안은_모두_draft_다() -> None:
    entries = build_draft(sync(["소금", "돼기고기", "은박컵", "육수", "식용유/소금/참기름/잣가루"]))

    assert {e["status"] for e in entries} == {"draft"}


def test_초안은_정리_표로_그대로_읽힌다() -> None:
    entries = build_draft(sync(["소금", "[절임용 소금물] 소금", "다진 마늘", "다진마늘", "안심"]))

    table = table_of(entries)  # 같은 키가 둘이면 여기서 실패한다

    assert set(table) == {"소금", "다진마늘", "안심"}


def test_같은_키의_원문은_하나로_모으고_많이_나온_표기를_쓴다() -> None:
    entries = build_draft(sync(["[절임용 소금물] 소금", "소금"], ["소금"]))

    [salt] = entries
    assert salt["raw"] == "소금"
    assert salt["rows"] == 3


def test_많이_나온_재료가_먼저다() -> None:
    entries = build_draft(sync(["양파", "소금"], ["소금"], ["소금", "양파", "두부"]))

    assert [e["raw"] for e in entries] == ["소금", "양파", "두부"]


def test_이름이_빈_재료는_초안에_없다() -> None:
    # 이름이 없으면 표로 정리할 수 없다 — 그 레시피는 그대로 해석되지 않는다
    assert [e["raw"] for e in build_draft(sync(["소금", None]))] == ["소금"]


# ── ② 후보 ────────────────────────────────────────────────────────────
def test_오타는_정리한_이름과_코드_후보를_단다() -> None:
    entry = draft_entry("돼기고기", rows=6)

    assert entry["names"] == ["돼지고기"]
    assert 10 in entry["codes"]
    assert "오타" in entry["note"]


def test_묶음_표기를_나누고_숨은_알레르기를_찾는다() -> None:
    entry = draft_entry("식용유/소금/참기름/잣가루", rows=1)

    assert entry["names"] == ["식용유", "소금", "참기름", "잣가루"]
    assert 19 in entry["codes"]  # 잣


@pytest.mark.parametrize(
    ("raw", "names"),
    [("소금, 후추", ["소금", "후추"]), ("홍피망, 청피망 각각", ["홍피망", "청피망"])],
)
def test_쉼표_묶음도_나눈다(raw: str, names: list[str]) -> None:
    assert draft_entry(raw, rows=1)["names"] == names


def test_섹션_머리는_떼고_정리한다() -> None:
    entry = draft_entry("[쇠고기양념] 간장", rows=1)

    assert entry["names"] == ["간장"]
    assert 16 not in entry["codes"]  # 섹션 제목의 쇠고기는 이 재료가 아니다


def test_괄호_속_고기는_코드_후보가_된다() -> None:
    entry = draft_entry("다짐육(돼지고기)", rows=1)

    assert 10 in entry["codes"]


@pytest.mark.parametrize(
    ("raw", "codes"),
    [
        ("안심", {10, 16}),  # 소 · 돼지 둘 다
        ("감동젓", {9}),  # 작은 새우젓
        ("선지", {10, 16}),
        ("다짐육", {10, 16}),
        ("장조림", {16}),
    ],
)
def test_부위_이름은_안전_사전이_넓은_코드를_단다(raw: str, codes: set[int]) -> None:
    # 스크립트만 알던 이름을 안전 사전(food_safety_terms.yaml)으로 옮겼다 — 한 곳을 고치면
    # 레시피 정리 표와 안전 필터가 같이 바뀐다(#326 리뷰)
    assert codes <= set(draft_entry(raw, rows=1)["codes"])


def test_스크립트에는_사전에_넣을_수_없는_한_글자_이름만_남는다() -> None:
    # 사전은 글 속 부분 일치라 "양"(소의 위)이 양파 · 양념에 걸린다. 스크립트는 재료 이름 전체가
    # 같을 때만 보므로 여기 남겨도 양파를 막지 않는다
    assert set(EXTRA_CODES) == {"양"}
    entry = draft_entry("양", rows=1)

    assert 16 in entry["codes"]
    assert entry["note"]  # 왜 그 코드인지 검수자가 본다
    assert 16 not in draft_entry("양파", rows=1)["codes"]


def test_사전이_아는_이름은_사전_코드를_그대로_쓴다() -> None:
    assert draft_entry("계란", rows=1)["codes"] == [1]
    assert draft_entry("양파", rows=1)["codes"] == []


def test_음식이_아닌_것은_이름을_비우고_표시만_한다() -> None:
    entry = draft_entry("은박컵", rows=1)

    assert entry["names"] == []
    assert entry["status"] == "draft"  # not_food 로 정하는 것은 검수자다
    assert "not_food" in entry["note"]


def test_구성을_알_수_없는_재료는_표시만_한다() -> None:
    entry = draft_entry("육수", rows=1)

    assert entry["status"] == "draft"
    assert "unknown" in entry["note"]


# ── ③ 검수한 항목을 덮지 않는다 ──────────────────────────────────────
def test_다시_돌려도_검수한_항목은_그대로다() -> None:
    reviewed = {
        "raw": "안심",
        "names": ["쇠고기"],
        "codes": [16],
        "status": "ok",
        "note": "소 안심",
    }
    fresh = build_draft(sync(["안심", "양파"]))

    merged = merge_entries([reviewed], fresh)

    assert merged[0] == reviewed
    assert [e["raw"] for e in merged] == ["안심", "양파"]


def test_다시_돌리면_새_이름만_더한다() -> None:
    existing = build_draft(sync(["소금"]))
    fresh = build_draft(sync(["소금", "새재료"]))

    merged = merge_entries(existing, fresh)

    assert [e["raw"] for e in merged] == ["소금", "새재료"]
    table_of(merged)  # 키가 겹치지 않는다


def test_데이터에서_사라진_이름도_지우지_않는다() -> None:
    # 검수 기록이 사라지면 같은 이름이 다시 나왔을 때 처음부터 다시 봐야 한다
    existing = [{"raw": "옛재료", "names": ["옛재료"], "codes": [], "status": "ok"}]

    merged = merge_entries(existing, build_draft(sync(["소금"])))

    assert [e["raw"] for e in merged] == ["옛재료", "소금"]


# ── 파일로 쓰기 ───────────────────────────────────────────────────────
def test_쓴_파일은_정리_표_로더로_그대로_읽힌다() -> None:
    entries = build_draft(sync(["돼기고기", "소금, 후추", '따옴표"재료', "은박컵"]))

    text = render_yaml(entries, fetched_at="2026-10-11")
    data = yaml.safe_load(text)
    table = parse_ingredient_table(data)

    assert [e["raw"] for e in data["ingredients"]] == [e["raw"] for e in entries]
    assert table["돼기고기"].names == ("돼지고기",)
    assert table["소금후추"].names == ("소금", "후추")
    assert "검수" in text  # 머리 주석에 검수 규칙이 있다


# ── 정리 표를 다시 써도 기존 항목이 바뀌지 않는다 (#510) ───────────────────
REAL_TABLE = Path(__file__).resolve().parents[3] / "reference" / "recipe_ingredients.yaml"


def test_food_group_도_그대로_다시_쓴다() -> None:
    entries = [
        {
            "raw": "설탕",
            "names": ["설탕"],
            "codes": [],
            "status": "ok",
            "food_group": "fats_sugars",
            "rows": 217,
        },
        {"raw": "소금", "names": ["소금"], "codes": [], "status": "ok", "rows": 346},
    ]

    text = render_yaml(entries, fetched_at="2026-11-02")

    assert yaml.safe_load(text)["ingredients"] == entries
    assert parse_ingredient_table(yaml.safe_load(text))["설탕"].food_group == "fats_sugars"


def test_실제_정리_표를_다시_써도_항목이_하나도_바뀌지_않는다() -> None:
    """초안 스크립트는 검수한 값을 덮지 않는다. 다시 쓸 때 칸 하나를 빠뜨리면(`food_group`) 사람이
    정한 식품군이 조용히 사라진다 — 지금 파일을 그대로 한 번 돌려서 본다."""
    original = yaml.safe_load(REAL_TABLE.read_text(encoding="utf-8"))

    rewritten = yaml.safe_load(
        render_yaml(original["ingredients"], fetched_at="2026-11-02", version=original["version"])
    )

    assert rewritten["ingredients"] == original["ingredients"]
    counts = Counter(entry["status"] for entry in original["ingredients"])
    assert Counter(entry["status"] for entry in rewritten["ingredients"]) == counts
    grouped = sum(1 for entry in original["ingredients"] if "food_group" in entry)
    assert sum(1 for entry in rewritten["ingredients"] if "food_group" in entry) == grouped > 0


# ── --source 와 파일 갱신 ─────────────────────────────────────────────
def _write_sync(tmp_path: Path, *recipes: list[str | None]) -> Path:
    path = tmp_path / "sync.json"
    path.write_text(json.dumps(sync(*recipes), ensure_ascii=False), encoding="utf-8")
    return path


EXISTING = """\
# 손으로 고친 머리말 — 이 줄은 다시 써도 남는다
# 🚨 검수 기록은 사람만 바꾼다

source: "농식품정보원 레시피 재료정보 — 재료 이름별 정리 (초안 스크립트 + 사람 검수)"
version: "2026.1"
fetched_at: 2026-10-11

ingredients:
  - raw: "설탕"
    names: ["설탕"]
    codes: []
    status: ok
    food_group: fats_sugars
    rows: 217
  - raw: "안심"
    names: ["쇠고기"]
    codes: [16]
    status: ok
    food_group: meat_fish_egg_legumes
    note: "소 안심"
    rows: 3
"""


def raw_sync(*names: str) -> RecipeSync:
    """받기 결과(RecipeSync) — 공용 받기 모듈(`scripts/recipe_fetch`)이 돌려주는 모양."""
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
        return raw_sync("가짜마파")

    async def fake_nics():
        calls.append("nics")
        return raw_sync("가짜닉스")

    monkeypatch.setattr(recipe_fetch, "fetch_mafra", fake_mafra)
    monkeypatch.setattr(recipe_fetch, "fetch_nics", fake_nics)
    return calls


def test_source_는_정해진_이름만_받는다() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--fetch", "--source", "unknown"])


def test_source_없이_받으면_농식품정보원이다(tmp_path, fetchers) -> None:
    main(["--fetch", "--output", str(tmp_path / "table.yaml")])

    assert fetchers == ["mafra"]


def test_받아_둔_결과에는_source_를_붙일_수_없다(tmp_path, fetchers) -> None:
    # 받아 둔 결과는 이미 소스가 정해져 있다 — 붙여도 무시되고 "소스: ..." 만 틀리게 찍힌다
    with pytest.raises(SystemExit):
        main(
            ["--input", str(_write_sync(tmp_path, ["소금"])), "--source", "nics"]
            + ["--output", str(tmp_path / "table.yaml")]
        )
    assert fetchers == []


def test_다시_돌려도_머리말과_version_과_검수한_항목은_그대로고_새_이름만_끝에_붙는다(
    tmp_path,
) -> None:
    out = tmp_path / "table.yaml"
    out.write_text(EXISTING, encoding="utf-8")
    before = yaml.safe_load(EXISTING)["ingredients"]

    code = main(["--input", str(_write_sync(tmp_path, ["안심", "새재료"])), "--output", str(out)])

    text = out.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert code == 0
    assert text.startswith("# 손으로 고친 머리말")  # 머리말은 갈아엎지 않는다
    assert data["version"] == "2026.1"
    assert data["ingredients"][: len(before)] == before  # 검수한 항목 · food_group 그대로
    [added] = data["ingredients"][len(before) :]
    assert (added["raw"], added["status"]) == ("새재료", "draft")
    assert "농촌진흥청" in data["source"] and "농식품정보원" in data["source"]


def test_파일이_없으면_새로_만든다(tmp_path) -> None:
    out = tmp_path / "new.yaml"

    main(["--input", str(_write_sync(tmp_path, ["소금"])), "--output", str(out)])

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert [e["status"] for e in data["ingredients"]] == ["draft"]
    parse_ingredient_table(data)


def test_nics_로_받으면_NICS_클라이언트만_쓴다(tmp_path, fetchers) -> None:
    out = tmp_path / "table.yaml"

    main(["--fetch", "--source", "nics", "--output", str(out)])

    assert fetchers == ["nics"]
    assert [e["raw"] for e in yaml.safe_load(out.read_text(encoding="utf-8"))["ingredients"]] == [
        "가짜닉스"
    ]


def test_nics_로_받는데_키가_없으면_부르지_않고_멈춘다(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "NICS_RECIPE_API_KEY", "")

    with pytest.raises(NicsApiError):
        main(["--fetch", "--source", "nics", "--output", str(tmp_path / "table.yaml")])

    assert not (tmp_path / "table.yaml").exists()  # 받지 못했으면 파일을 만들지 않는다


def test_도움말은_cp949_콘솔에서도_찍힌다() -> None:
    build_parser().format_help().encode("cp949")
