"""레시피 재료 정리 표 검수 순서 — 가장 적게 검수해서 레시피를 가장 많이 푸는 순서 (FT-18).

    uv run python -m scripts.recipe_review_order --fetch --target 50 --output review_order.txt
    uv run python -m scripts.recipe_review_order --input SYNC.json --target 50

남은 재료(정리 표에 없거나 `draft`)가 가장 적은 레시피부터 고르고, 그 레시피의 남은 재료를 순서에
더한다. `unknown` 이거나 이름이 빈 재료가 든 레시피, 재료가 하나도 없는 레시피는 검수해도 풀리지
않아 고르지 않는다(`build_recipe_row` 가 `resolved=false` 로 두는 경우와 같다). 출력은 재료 원문
이름과 그 재료까지 봤을 때 풀리는 레시피 수다 — 검수 자체(`ok` · 코드)는 사람이 한다.

🚨 이 스크립트는 `status` 를 바꾸지 않는다. 알레르기 코드는 사람만 고른다(정리 표 머리말).
"""

import argparse
import asyncio
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.agents.food.recipe_catalog import IngredientEntry, ingredient_key, ingredient_table

# `recipe_catalog._SETTLED` 와 같은 값이다 — 이 둘만 정리된 것으로 센다. 어긋나면 순서가 약속한
# 레시피 수와 실제 `resolved` 수가 달라져서, 둘이 같은지 테스트가 `build_recipe_row` 로 맞춰 본다
_SETTLED = frozenset({"ok", "not_food"})


def _remaining(
    names: Sequence[str | None], table: Mapping[str, IngredientEntry]
) -> set[str] | None:
    """검수가 남은 재료 키. 풀 수 없는 레시피면 None."""
    if not names:
        return None  # 재료가 하나도 없으면 build_recipe_row 가 resolved=False 로 둔다
    left: set[str] = set()
    for name in names:
        key = ingredient_key(name) if name else ""
        if not key:
            return None
        entry = table.get(key)
        if entry is not None and entry.status == "unknown":
            return None
        if entry is None or entry.status not in _SETTLED:
            left.add(key)
    return left


def review_order(
    recipes: Sequence[Sequence[str | None]],
    table: Mapping[str, IngredientEntry],
    *,
    target: int,
) -> list[tuple[str, int]]:
    needs = [left for names in recipes if (left := _remaining(names, table)) is not None]
    chosen: set[str] = set()
    order: list[tuple[str, int]] = []

    def unlocked() -> int:
        return sum(1 for left in needs if left <= chosen)

    while unlocked() < target:
        open_ = [left - chosen for left in needs if not left <= chosen]
        if not open_:
            break
        smallest = min(open_, key=lambda left: (len(left), sorted(left)))
        for key in sorted(smallest):
            chosen.add(key)
            order.append((key, unlocked()))
    return order


def _recipes_from(sync: dict[str, Any]) -> list[list[str | None]]:
    return [
        [item.get("name") for item in recipe.get("ingredients") or []]
        for recipe in sync.get("recipes") or []
    ]


async def _fetch() -> dict[str, Any]:
    from app.core.config import settings
    from app.integrations.mafra.client import MafraRecipeClient, mafra_recipe_http

    async with mafra_recipe_http() as http:
        client = MafraRecipeClient(api_key=settings.MAFRA_RECIPE_API_KEY, http=http)
        return dataclasses.asdict(await client.recipes_with_ingredients())


def build_parser() -> argparse.ArgumentParser:
    # 모듈 docstring 을 쓰지 않는다. `—` · 이모지는 Windows 콘솔(cp949)에서 못 찍어
    # `--help` 가 죽는다
    parser = argparse.ArgumentParser(
        description="레시피 재료 정리 표 검수 순서. 적게 검수해서 레시피를 많이 푸는 순서로 재료 "
        "원문 이름과 그 재료까지 봤을 때 풀리는 레시피 누계를 낸다. status 는 바꾸지 않는다."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true", help="농식품정보원에서 받는다")
    source.add_argument("--input", type=Path, help="받아 둔 동기화 결과(JSON)")
    parser.add_argument("--target", type=int, default=50, help="풀고 싶은 레시피 수")
    parser.add_argument(
        "--output",
        type=Path,
        help="UTF-8 파일로 쓴다 (Windows 에서 > 로 리다이렉트하면 cp949 라 글자에 따라 죽는다)",
    )
    return parser


def main(
    argv: list[str] | None = None, *, table: Mapping[str, IngredientEntry] | None = None
) -> int:
    args = build_parser().parse_args(argv)

    sync = (
        asyncio.run(_fetch()) if args.fetch else json.loads(args.input.read_text(encoding="utf-8"))
    )
    entries = ingredient_table() if table is None else table
    lines = [
        f"{count:>4}  {entries[key].raw if key in entries else key}"
        for key, count in review_order(_recipes_from(sync), entries, target=args.target)
    ]
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8", newline="")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
