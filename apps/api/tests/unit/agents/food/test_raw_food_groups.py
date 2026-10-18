"""원재료성 행 식품군 (#459) — 실제 응답 모양으로 영양DB 행 → 메뉴 카탈로그 행.

픽스처(`tests/fixtures/mfds_nutri_sample.json`)의 우유 행이 실제 응답 그대로다 — 대분류 이름이
비고 코드(`13`)만 온다. 다른 원재료는 그 행을 복사해 이름 · 그룹 · 대분류 칸만 품목대표 전체에서
센 실제 값으로 바꾼다(영양값은 우유 그대로 — 식품군만 본다).
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.agents.food.catalog_rows import nutrition_catalog_rows
from app.integrations.mfds.client import MfdsNutriClient

NOW = datetime(2026, 10, 15, tzinfo=UTC)
SAMPLE = json.loads(
    (Path(__file__).resolve().parents[3] / "fixtures" / "mfds_nutri_sample.json").read_text(
        encoding="utf-8"
    )
)
DISH = SAMPLE["body"]["items"][0]  # 된장찌개 — 찌개 및 전골류는 대응표에 없다
MILK = next(item for item in SAMPLE["body"]["items"] if item["FOOD_NM_KR"] == "우유")


def raw_item(name: str, *, category: str | None, code: str, group: str = "원재료성") -> dict:
    return {
        **MILK,
        "FOOD_CD": f"R-{name}",
        "FOOD_NM_KR": name,
        "DB_GRP_NM": group,
        "FOOD_CAT1_NM": category,
        "FOOD_CAT1_CD": code,
    }


async def catalog(
    items: list[dict[str, Any]], *, borrowed: Mapping[str, frozenset[str]] | None = None
) -> dict[str, frozenset[str]]:
    body = {**SAMPLE, "body": {**SAMPLE["body"], "totalCount": len(items), "items": items}}
    client = MfdsNutriClient(
        service_key="test-service-key",
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    )
    rows = await client.nutrition_rows(name="가짜")
    return {
        row.display_name: row.food_groups
        for row in nutrition_catalog_rows(rows, now=NOW, borrowed=borrowed)
    }


async def test_원재료성_흔한_식품이_식품군을_갖는다() -> None:
    groups = await catalog(
        [
            MILK,  # 이름 빔 · 13
            raw_item("달걀", category=None, code="10"),
            raw_item("닭고기", category=None, code="09"),
            raw_item("쌀", category="곡류", code="01"),
            raw_item("고등어", category="어패류 및 기타 수산물", code="11"),
        ]
    )

    assert groups == {
        "우유": frozenset({"dairy"}),
        "달걀": frozenset({"meat_fish_egg_legumes"}),
        "닭고기": frozenset({"meat_fish_egg_legumes"}),
        "쌀": frozenset({"grains"}),
        "고등어": frozenset({"meat_fish_egg_legumes"}),
    }


async def test_이름이_비고_코드도_표에_없으면_비어_있다() -> None:
    groups = await catalog(
        [
            raw_item("미역", category=None, code="12"),
            raw_item("가짜원재료", category=None, code="21"),
        ]
    )

    assert groups == {"미역": frozenset(), "가짜원재료": frozenset()}


async def test_음식_행은_대분류_코드로_찾지_않는다() -> None:
    # 음식 13 은 나물·숙채류다 — 원재료성 13(우유)으로 읽으면 나물이 dairy 가 된다
    groups = await catalog([raw_item("가짜나물", category=None, code="13", group="음식")])

    assert groups == {"가짜나물": frozenset()}


async def test_대응표에_없는_메뉴만_빌린_식품군을_쓴다() -> None:
    borrowed = {"된장찌개": frozenset({"vegetables"}), "우유": frozenset({"grains"})}

    groups = await catalog([DISH, MILK], borrowed=borrowed)

    assert groups["된장찌개"] == frozenset({"vegetables"})
    assert groups["우유"] == frozenset({"dairy"})  # 대응표가 먼저다
