"""레시피 재료 정리 표 초안 — 사람이 검수할 후보를 만든다.

    uv run python -m scripts.draft_recipe_ingredients --fetch           # 농식품정보원에서 받아 초안
    uv run python -m scripts.draft_recipe_ingredients --input SYNC.json # 저장해 둔 동기화 결과로

출력은 `reference/recipe_ingredients.yaml`(`--output` 으로 바꿈). 이미 있으면 있는 항목은 그대로
두고 새 이름만 더한다. 사람이 검수한 항목을 덮지 않는다.

초안이 하는 일은 후보를 채우는 것뿐이다. 모든 항목은 `status: draft`이고, 정리 표가 `draft`인
재료가 하나라도 있는 레시피는 `resolved=false` 라 후보 풀에 들어가지 않는다.
`ok`·`not_food`·`unknown` 은 검수자가 정한다.

  - 섹션 머리("[쇠고기양념] 간장")를 떼고, 같은 키는 하나로 모은다(많이 나온 표기를 `raw` 로)
  - 묶음 표기("식용유/소금/참기름/잣가루" · "소금, 후추")를 나눈다
  - 오타 후보("돼기고기" → 돼지고기)를 정리한 이름으로 단다
  - 코드는 공용 사전(`menu_codes`)으로 훑고, 사전이 모르는 이름(부위 · 젓갈 · 다짐육)에는
    넓은 후보를 더한다 — 덜 막는 쪽으로 틀리지 않게. 검수자가 레시피를 보고 좁힌다
  - 음식이 아닌 것 · 구성을 알 수 없는 것은 `note` 에 표시만 한다
"""

import argparse
import asyncio
import dataclasses
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.agents.food.recipe_catalog import ingredient_key
from app.agents.food.tools.safety import menu_codes
from app.rules.term_match import normalize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reference" / "recipe_ingredients.yaml"

_SOURCE = "농식품정보원 레시피 재료정보 — 재료 이름별 정리 (초안 스크립트 + 사람 검수)"
_SECTION = re.compile(r"^\s*\[[^\]]*\]\s*")
_SPLIT = re.compile(r"\s*[/,·]\s*")
_SPACES = re.compile(r"\s+")

# 오타 · 표기 흔들림 후보 (정규화한 원문 → 정리한 이름). 확실한 것만 둔다 — 애매한 것은 검수자가
# 본다
TYPOS: dict[str, str] = {
    "돼기고기": "돼지고기",
    "마요네스": "마요네즈",
    "머드터드": "머스터드",
    "머스타드": "머스터드",
    "허니머스타드": "허니머스터드",
    "다진생각": "다진생강",
    "소급": "소금",
    "참쌀": "찹쌀",
    "파마산치즈치즈": "파마산치즈",
    "파마르산치즈": "파마산치즈",
    "가쓰오브시": "가쓰오부시",
    "가쯔오브시": "가쓰오부시",
    "가쓰오브시국물": "가쓰오부시국물",
    "토마토케찹": "토마토케첩",
    "고추가루": "고춧가루",
    "토마토페스트": "토마토페이스트",
    "쭈꾸미": "주꾸미",
    "콘프레이크": "콘플레이크",
}

# 공용 사전이 모르는 이름 → 19종 코드 후보와 이유. 넓게 단다(애매하면 막는다)
EXTRA_CODES: dict[str, tuple[frozenset[int], str]] = {
    "안심": (frozenset({10, 16}), "소 · 돼지 안심 둘 다 — 레시피를 보고 좁힌다"),
    "선지": (frozenset({10, 16}), "소 · 돼지 피 — 레시피를 보고 좁힌다"),
    "곱창": (frozenset({10, 16}), "소 · 돼지 곱창 — 레시피를 보고 좁힌다"),
    "다짐육": (frozenset({10, 16}), "고기 종류를 모르는 다진 고기"),
    "고기산적": (frozenset({10, 16}), "고기 종류를 모르는 산적"),
    "쇠뼈": (frozenset({16}), "소뼈 — 육수에 쇠고기 성분"),
    "소잡뼈": (frozenset({16}), "소뼈 — 육수에 쇠고기 성분"),
    "쇠꼬리": (frozenset({16}), "소꼬리"),
    "소꼬리": (frozenset({16}), "소꼬리"),
    "양": (frozenset({16}), "소의 위(양) — 곰탕 재료"),
    "장조림": (frozenset({16}), "쇠고기 장조림 — 간장(밀 · 대두)은 제품마다"),
    "육회": (frozenset({16}), "날 쇠고기 — 연령 규칙(날음식)으로도 막힌다"),
    "순대": (frozenset({10}), "돼지 창자 — 속 재료(당면 · 찹쌀 · 선지)는 제품마다"),
    "감동젓": (frozenset({9}), "작은 새우젓(곤쟁이젓)"),
    "햄": (frozenset({10}), "대개 돼지고기 — 제품에 따라 닭 · 대두 · 밀 · 우유"),
    "슬라이스햄": (frozenset({10}), "대개 돼지고기 — 제품에 따라 닭 · 대두 · 밀 · 우유"),
    "네모난햄": (frozenset({10}), "대개 돼지고기 — 제품에 따라 닭 · 대두 · 밀 · 우유"),
    "베이컨": (frozenset({10}), "대개 돼지고기"),
    "비엔나소시지": (frozenset({10}), "대개 돼지고기 — 제품에 따라 닭 · 대두 · 밀 · 우유"),
    "프랑크소시지": (frozenset({10}), "대개 돼지고기 — 제품에 따라 닭 · 대두 · 밀 · 우유"),
}

# 음식이 아닌 것으로 보이는 이름 — 검수자가 not_food 로 정한다
NOT_FOOD: frozenset[str] = frozenset({"은박컵", "랩", "무명실", "숯", "꼬치"})

# 이름만으로 구성을 알 수 없는 것(혼합 · 상표) — 검수자가 unknown 으로 둘지 정한다
UNKNOWN: frozenset[str] = frozenset(
    {
        "육수",
        "맛국물",
        "소스",
        "양념",
        "양념장",
        "양념간장",
        "조미료",
        "허브솔트",
        "시판용장국",
        "청정원맛선생",
        "청정원국선생",
        "청정원어장",
        "청정원굴소스",
        "청정원순창쌈장",
        "햇살담은간장",
        "아기된장소스",
        "페스토소스",
        "돈까스소스",
        "바베큐소스",
        "레몬드레싱",
        "오렌지마요네즈",
        "크림수프",
        "냉동만두",
        "과일통조림",
        "후루츠칵테일",
        "오곡곡물",
        "모듬채소",
        "샐러드채소",
    }
)

_HEADER = """\
# 레시피 재료 정리 표 — reference/recipe_ingredients.yaml
# 설계: app/agents/food/recipe_catalog.py · docs/agents/food/food_agent_own_table.md §1
#
# 알레르기 판정 입력으로, 모델이 고르지 않는다.
# status 가 ok · not_food 인 재료만 정리된 것으로 센다. draft(검수 전) · unknown(구성 모름) ·
# 표에 없는 재료가 하나라도 있는 레시피는 resolved=false — 후보 풀에 들어가지 않는다.
#
# 검수하는 법 (한 줄씩)
#   names   정리한 재료 이름. 묶음 표기는 나눈다. 사전이 읽을 수 있는 이름으로 적는다
#           (돼기고기 → 돼지고기)
#   codes   이 재료에 확실히 든 19종 코드. 제품마다 다른 성분(간장의 밀)은 넣지 않는다 — 이름이
#           남아 있으면 필터가 따로 본다. 애매하면 넣는다(더 막는 쪽)
#   status  ok        이름 · 코드를 확인했다
#           not_food  음식이 아니다(은박컵 · 랩). names 를 비운다
#           unknown   이름만으로 구성을 알 수 없다(육수 · 상표 양념) — 그 레시피는 후보에서 빠진다
#           draft     아직 보지 않았다 (초안 스크립트가 만든 값)
#   note    초안이 붙인 후보 이유. 검수한 사람 · 날짜를 적어 둔다
#   rows    이 이름이 나온 재료 행 수 — 많이 나온 것부터 보면 레시피가 빨리 풀린다
#
# 다시 만들기: uv run python -m scripts.draft_recipe_ingredients --fetch  (있는 항목은 덮지 않는다)
"""


def _clean(raw: str) -> str:
    text = _SECTION.sub("", unicodedata.normalize("NFKC", raw))
    return _SPACES.sub(" ", text).strip()


def draft_entry(raw: str, *, rows: int) -> dict[str, Any]:
    """원문 이름 하나의 초안. 항상 `status: draft` 다."""
    text = _clean(raw)
    base = text.removesuffix("각각").strip()
    parts = [part for part in _SPLIT.split(base) if part]
    notes: list[str] = []
    if len(parts) > 1:
        notes.append("묶음 표기를 나눔")

    names: list[str] = []
    for part in parts:
        fixed = TYPOS.get(normalize(part))
        if fixed:
            notes.append(f"오타 후보: {part} → {fixed}")
        names.append(fixed or part)

    codes = set(menu_codes((text, *names)))
    for part, name in zip(parts, names, strict=True):
        extra = EXTRA_CODES.get(normalize(part)) or EXTRA_CODES.get(normalize(name))
        if extra:
            codes |= extra[0]
            notes.append(f"사전이 모르는 이름 — {extra[1]}")

    key = normalize(text)
    if key in NOT_FOOD:
        names = []
        notes.append("음식이 아닌 것으로 보임 → not_food 후보")
    if key in UNKNOWN:
        notes.append("이름만으로 구성을 알 수 없음 → unknown 후보")

    return {
        "raw": raw.strip(),
        "names": names,
        "codes": sorted(codes),
        "status": "draft",
        "note": "; ".join(notes),
        "rows": rows,
    }


def build_draft(sync: dict[str, Any]) -> list[dict[str, Any]]:
    """동기화 결과(RecipeSync 를 JSON 으로)에서 재료 이름마다 초안. 많이 나온 이름부터."""
    rows: Counter[str] = Counter()
    variants: dict[str, Counter[str]] = {}
    first_seen: dict[str, int] = {}
    for recipe in sync.get("recipes") or []:
        for item in recipe.get("ingredients") or []:
            name = item.get("name")
            if not name or not (key := ingredient_key(name)):
                continue  # 이름이 없으면 표로 정리할 수 없다 — 그 레시피는 해석되지 않는다
            rows[key] += 1
            variants.setdefault(key, Counter())[name.strip()] += 1
            first_seen.setdefault(key, len(first_seen))
    keys = sorted(rows, key=lambda key: (-rows[key], first_seen[key]))
    return [draft_entry(variants[key].most_common(1)[0][0], rows=rows[key]) for key in keys]


def merge_entries(
    existing: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """있는 항목은 그대로 두고(검수 기록) 새 키만 뒤에 더한다.

    데이터에서 사라진 이름도 지우지 않는다 — 같은 이름이 다시 나오면 검수를 처음부터 다시 해야 한다.
    """
    seen = {ingredient_key(str(entry.get("raw") or "")) for entry in existing}
    added = [entry for entry in fresh if ingredient_key(entry["raw"]) not in seen]
    return [*existing, *added]


def render_yaml(entries: list[dict[str, Any]], *, fetched_at: str) -> str:
    """정리 표 파일. 값은 JSON 문자열로 적는다(YAML 로 그대로 읽힌다)."""

    def text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = [
        _HEADER,
        f"source: {text(_SOURCE)}",
        'version: "2026.1-draft"',
        f"fetched_at: {fetched_at}",
        "",
        "ingredients:",
    ]
    for entry in entries:
        lines.append(f"  - raw: {text(entry['raw'])}")
        lines.append(f"    names: [{', '.join(text(name) for name in entry.get('names') or [])}]")
        lines.append(f"    codes: [{', '.join(str(code) for code in entry.get('codes') or [])}]")
        lines.append(f"    status: {entry['status']}")
        if entry.get("note"):
            lines.append(f"    note: {text(entry['note'])}")
        if entry.get("rows") is not None:
            lines.append(f"    rows: {entry['rows']}")
    return "\n".join(lines) + "\n"


async def _fetch() -> dict[str, Any]:
    from app.core.config import settings
    from app.integrations.mafra.client import MafraRecipeClient, mafra_recipe_http

    async with mafra_recipe_http() as http:
        client = MafraRecipeClient(api_key=settings.MAFRA_RECIPE_API_KEY, http=http)
        return dataclasses.asdict(await client.recipes_with_ingredients())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input", type=Path, help="RecipeSync 를 JSON 으로 저장한 파일 (저장소 밖)"
    )
    source.add_argument(
        "--fetch", action="store_true", help="농식품정보원에서 바로 받는다 (키 필요)"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    sync = (
        json.loads(args.input.read_text(encoding="utf-8")) if args.input else asyncio.run(_fetch())
    )
    fresh = build_draft(sync)
    existing: list[dict[str, Any]] = []
    if args.output.exists():
        existing = list(
            (yaml.safe_load(args.output.read_text(encoding="utf-8")) or {}).get("ingredients") or []
        )
    merged = merge_entries(existing, fresh)
    args.output.write_text(
        render_yaml(merged, fetched_at=date.today().isoformat()), encoding="utf-8"
    )

    status = Counter(entry["status"] for entry in merged)
    print(
        f"정리 표: {len(merged)}개 (있던 것 {len(existing)} · 새로 {len(merged) - len(existing)})"
    )
    print(f"  status: {dict(status)}")
    print(f"  코드 후보가 있는 이름: {sum(1 for e in merged if e.get('codes'))}")
    print(f"  출력: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
