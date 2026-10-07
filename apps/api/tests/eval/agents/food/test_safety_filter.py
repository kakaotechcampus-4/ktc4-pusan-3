"""Food 안전 필터 라이브 eval — 모델이 쓸 법한 글을 코드 필터가 다 잡는가 (#258).

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/food/test_safety_filter.py -m live

Food Agent 는 아직 모델을 부르지 않는 mock 이라, 에이전트를 거치지 않고 필터를 직접 본다.
FOOD_* 모델에게 테스트 글을 쓰게 하고(보호자가 적는 알레르기 이름 · 그 성분이 든 메뉴 ·
그 성분이 없는 메뉴), 필터가 어떻게 판정하는지 본다. 사례표(test_food_safety_cases.py)는
사람이 떠올린 입력이고, 이 eval 은 모델이 만든 변형이라 사전의 빈 곳을 찾는 용도다.

- 이름 읽기: 보호자 표현마다, 그 성분이 확실히 든 기준 메뉴가 막혀야 한다.
- 든 메뉴: 그 성분이나 그것으로 만든 재료(우유 → 버터 · 치즈)가 든 메뉴는 정식 이름으로
  등록한 아이에게 전부 막혀야 한다.
- 시판 가공식품 메뉴: 판정하지 않고 기록만 한다. "들어 있을 수도 있는" 성분(카레가루의 우유 ·
  어묵의 새우)은 제품 표시를 봐야 해서, 사전에 넣을지 정할 후보 목록으로 쓴다.
- 없는 메뉴: 막는 쪽이 기본이라 조금 막혀도 되지만, 절반 넘게 막히면 사전이 너무 넓다.
- 연령 규칙: 그 위험 식품 메뉴가 기준 월령에서 막히거나(주의는 주의가 붙어야) 한다.

생성한 글과 판정은 test_safety_filter_results.jsonl 에 남긴다. 실제 사용자 글은 없다.
기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety

pytestmark = pytest.mark.live

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
OLDER = 60  # 영아 규칙 · 질식 주의가 끼지 않는 월령
RESULTS = Path(__file__).with_name("test_safety_filter_results.jsonl")
LABELS_PER_CASE = 8
CONTAINING_PER_CASE = 8
PROCESSED_PER_CASE = 4
FREE_PER_CASE = 6
FREE_BLOCKED_LIMIT = FREE_PER_CASE // 2  # 없는 메뉴가 이보다 많이 막히면 사전이 너무 넓다


class Menu(BaseModel):
    name: str
    ingredients: list[str]


class ContainingMenu(Menu):
    allergen_ingredient: str  # 그 성분이 든 재료(모델이 고른 것) — 실패를 읽을 때 쓴다


class AllergyCase(BaseModel):
    labels: list[str]
    containing: list[ContainingMenu]
    processed: list[ContainingMenu]
    free: list[Menu]


class HazardCase(BaseModel):
    menus: list[Menu]


# (정식 이름, kind, 설명, 그 성분이 확실히 든 기준 메뉴)
ALLERGIES: list[tuple[str, str, str, tuple[str, tuple[str, ...]]]] = [
    ("난류", "allergy", "달걀 · 메추리알", ("계란찜", ("계란",))),
    ("우유", "allergy", "우유와 유제품", ("우유푸딩", ("우유",))),
    ("메밀", "allergy", "메밀", ("메밀국수", ("메밀면",))),
    ("땅콩", "allergy", "땅콩", ("땅콩조림", ("땅콩",))),
    ("대두", "allergy", "콩(대두)과 콩으로 만든 것", ("두부조림", ("두부",))),
    ("밀", "allergy", "밀과 밀가루", ("칼국수", ("밀가루",))),
    ("고등어", "allergy", "고등어", ("고등어구이", ("고등어",))),
    ("게", "allergy", "게(꽃게 · 대게 …)", ("꽃게탕", ("꽃게",))),
    ("새우", "allergy", "새우", ("새우볶음밥", ("새우",))),
    ("돼지고기", "allergy", "돼지고기", ("제육볶음", ("돼지고기",))),
    ("복숭아", "allergy", "복숭아", ("복숭아 화채", ("복숭아",))),
    ("토마토", "allergy", "토마토", ("토마토 스튜", ("토마토",))),
    (
        "아황산류",
        "allergy",
        "아황산염(보존료) — 첨가물이라 든 메뉴는 재료에 첨가물 이름"
        "(아황산나트륨 · 메타중아황산칼륨 …)을 적는다",
        ("말린 살구", ("아황산나트륨",)),
    ),
    ("호두", "allergy", "호두", ("호두강정", ("호두",))),
    ("닭고기", "allergy", "닭고기", ("닭죽", ("닭고기",))),
    ("쇠고기", "allergy", "쇠고기", ("소고기 미역국", ("소고기",))),
    ("오징어", "allergy", "오징어", ("오징어볶음", ("오징어",))),
    ("조개류", "allergy", "조개 · 굴 · 전복 · 홍합", ("바지락 된장국", ("바지락",))),
    ("잣", "allergy", "잣", ("잣죽", ("잣",))),
    ("참깨", "allergy", "참깨(19종 밖)", ("시금치나물", ("참기름", "깨소금"))),
    ("키위", "allergy", "키위(19종 밖)", ("키위 주스", ("키위",))),
    ("아몬드", "allergy", "아몬드(19종 밖)", ("아몬드 쿠키", ("아몬드",))),
    ("연어", "allergy", "연어(19종 밖)", ("연어 구이", ("연어",))),
    ("갑각류", "allergy", "갑각류 전체 — 게 · 새우 · 가재 …", ("새우볶음밥", ("새우",))),
    ("견과류", "allergy", "견과류 전체 — 호두 · 아몬드 · 잣 …", ("호두강정", ("호두",))),
    (
        "해산물",
        "allergy",
        "해산물 전체 — 생선 · 갑각류 · 조개 · 오징어",
        ("오징어볶음", ("오징어",)),
    ),
    ("셀리악병", "chronic_disease", "글루텐을 먹으면 안 되는 질환", ("칼국수", ("밀가루",))),
    (
        "유당불내증",
        "chronic_disease",
        "우유의 유당을 소화하지 못하는 질환",
        ("우유푸딩", ("우유",)),
    ),
]

# (규칙 label, 월령, 판정, 모델에게 줄 설명)
HAZARDS: list[tuple[str, int, str, str]] = [
    ("꿀", 6, "blocked", "꿀(벌꿀 · 허니)이 들어간 간식이나 요리"),
    (
        "생우유",
        6,
        "blocked",
        "마시는 우유 그 자체 — 메뉴명이 우유 음료다(흰우유 · 우유 한 잔 · 딸기우유)",
    ),
    (
        "질식 위험 식품",
        30,
        "blocked",
        "통째로 주면 목에 걸리는 간식 — 사탕 · 젤리 · 팝콘 · 마시멜로 · 껌"
        "(솜사탕처럼 입에서 녹는 것은 아니다)",
    ),
    (
        "질식 주의 식품",
        30,
        "caution",
        "잘라서 줘야 하는 음식 — 떡 · 포도 · 방울토마토 · 소시지 · 견과"
        "(떡갈비처럼 이름에만 떡이 든 것은 아니다)",
    ),
    (
        "날음식",
        OLDER,
        "blocked",
        "날것으로 먹는 음식 — 회 · 초밥 · 육회 · 생굴 · 간장게장 · 날달걀",
    ),
    (
        "카페인",
        OLDER,
        "blocked",
        "카페인 음료 그 자체 — 메뉴명이 커피 · 녹차 · 홍차 · 콜라 같은 음료다",
    ),
]


def _format(model: type[BaseModel], name: str) -> dict[str, Any]:
    schema = to_strict_json_schema(model)
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}


async def _ask(prompt: str, model: type[BaseModel]) -> Any:
    client = LLMClient(AgentSettings(), role="food")
    response = await client.chat(
        messages=[
            {
                "role": "system",
                "content": "너는 한국 육아 앱의 안전 필터를 시험하는 데이터 생성기다. "
                "한국 가정 · 어린이집에서 실제로 쓰는 말투와 메뉴로 쓴다. 지시한 개수를 지킨다.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format=_format(model, model.__name__),
    )
    return model.model_validate_json(response.message.content or "{}")


def _row(name: str, ingredients: tuple[str, ...] | list[str]) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=name,
        display_name=name,
        source="manual",
        resolved=True,
        synced_at=NOW,
        ingredients=tuple(ingredients),
    )


def _verdict(row: MenuCatalogRow, entries: list[SafetyEntry], months: int) -> tuple[str, Any]:
    result = filter_food_safety([row], entries, "preschool", months=months)
    verdict = "blocked" if result.blocked else "unchecked" if result.unchecked else "passed"
    return verdict, result


def _record(kind: str, case: str, data: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"kind": kind, "case": case, **data}, ensure_ascii=False) + "\n")


@pytest.mark.parametrize(
    ("name", "kind", "description", "probe"),
    ALLERGIES,
    ids=[name for name, *_ in ALLERGIES],
)
async def test_알레르기_이름과_메뉴(
    name: str,
    kind: str,
    description: str,
    probe: tuple[str, tuple[str, ...]],
    expect: Callable[..., None],
) -> None:
    expect(
        f"보호자가 '{name}' 을 적는 표현 {LABELS_PER_CASE}개 모두 기준 메뉴 '{probe[0]}' 를 막는다",
        f"'{name}' 이 든 메뉴 {CONTAINING_PER_CASE}개가 모두 막힌다",
        f"'{name}' 이 없는 메뉴 {FREE_PER_CASE}개 중 막히는 것이 {FREE_BLOCKED_LIMIT}개 이하다",
    )
    # 묶음은 구성 식품을 늘어놓으면(생선 · 갑각류 · 조개) 기준 메뉴가 빠질 수 있다
    group = (
        "묶음 이름 자체를 다르게 적은 것만 쓴다 — 구성 식품을 늘어놓지 않는다."
        if "전체" in description
        else ""
    )
    data: AllergyCase = await _ask(
        f"""알레르기 · 질환: {name} ({description})

1. labels: 보호자가 앱의 알레르기 칸에 이것을 직접 적는 서로 다른 표현 {LABELS_PER_CASE}개.
   띄어쓰기 · 조사 · 흔한 오타 · 꼬리말("알레르기 있어요" · "심함") · 검사지의 영어 이름 ·
   괄호 등급("class 3") 같은 실제 변형을 섞는다. 이 알레르기 하나만 뜻해야 한다. {group}
2. containing: 이 성분이 반드시 들어간 한국 가정 · 어린이집 메뉴 {CONTAINING_PER_CASE}개.
   ingredients 는 레시피처럼 적는다("우유 200ml"). 재료에는 그 성분 자체나 그것으로 만든
   재료(우유 → 버터 · 치즈 · 생크림, 밀 → 밀가루 · 빵가루 · 소면)를 쓴다. 메뉴명에 성분이 안
   드러나는 것을 섞는다. 시판 가공식품은 쓰지 않는다.
   allergen_ingredient 에 그 성분이 든 재료를 적는다.
2-1. processed: 시판 가공식품 · 양념(카레가루 · 어묵 · 소스 …)에만 이 성분이 들어가는 메뉴
   {PROCESSED_PER_CASE}개. allergen_ingredient 에 그 가공식품을 적는다.
3. free: 이 성분이 전혀 없는 메뉴 {FREE_PER_CASE}개.
   가공식품 · 양념에 숨어 들어가는 경우도 없게 한다. 메뉴명 · 재료에 그 성분 이름을 쓰지
   않는다("~ 없는" · "(성분 없음)" 같은 설명도 쓰지 않는다).""",
        AllergyCase,
    )

    canonical = [SafetyEntry(kind=kind, label=name, status="active")]  # type: ignore[arg-type]
    probe_row = _row(*probe)
    missed_labels = []
    for label in data.labels:
        entries = [SafetyEntry(kind=kind, label=label, status="active")]  # type: ignore[arg-type]
        verdict, _ = _verdict(probe_row, entries, OLDER)
        if verdict != "blocked":
            missed_labels.append(label)
    missed_menus = []
    for menu in data.containing:
        verdict, _ = _verdict(_row(menu.name, menu.ingredients), canonical, OLDER)
        if verdict != "blocked":
            missed_menus.append(f"{menu.name} {menu.ingredients} ← {menu.allergen_ingredient}")
    processed = []
    for menu in data.processed:
        verdict, _ = _verdict(_row(menu.name, menu.ingredients), canonical, OLDER)
        processed.append(f"{verdict} — {menu.allergen_ingredient} ({menu.name})")
    over_blocked = []
    for menu in data.free:
        verdict, result = _verdict(_row(menu.name, menu.ingredients), canonical, OLDER)
        if verdict == "blocked":
            over_blocked.append(f"{menu.name} {menu.ingredients} → {result.hits[menu.name]}")

    _record(
        "allergy",
        name,
        {
            "data": data.model_dump(),
            "missed_labels": missed_labels,
            "missed_menus": missed_menus,
            "processed": processed,
            "over_blocked": over_blocked,
        },
    )
    problems = []
    if missed_labels:
        problems.append(f"못 읽은 표현 {missed_labels}")
    if missed_menus:
        problems.append(f"통과한 메뉴 {missed_menus}")
    if len(over_blocked) > FREE_BLOCKED_LIMIT:
        problems.append(f"성분 없는데 막힌 메뉴 {over_blocked}")
    assert not problems, " / ".join(problems)


@pytest.mark.parametrize(
    ("rule", "months", "expected", "description"),
    HAZARDS,
    ids=[rule for rule, *_ in HAZARDS],
)
async def test_연령_아이_금지_식품(
    rule: str, months: int, expected: str, description: str, expect: Callable[..., None]
) -> None:
    action = "주의가 붙는다" if expected == "caution" else "막힌다"
    expect(f"{months}개월 아이에게 {description} 메뉴 {CONTAINING_PER_CASE}개가 모두 {action}")
    data: HazardCase = await _ask(
        f"""{description} — 이런 한국 메뉴 {CONTAINING_PER_CASE}개를 메뉴명과 재료 목록으로 써라.
재료는 레시피처럼 적는다. 메뉴명 · 재료 표기를 다양하게 섞는다.""",
        HazardCase,
    )

    missed = []
    for menu in data.menus:
        verdict, result = _verdict(_row(menu.name, menu.ingredients), [], months)
        caught = (
            rule in result.cautions.get(menu.name, ())
            if expected == "caution"
            else verdict == "blocked"
        )
        if not caught:
            missed.append(f"{menu.name} {menu.ingredients}")

    _record("hazard", rule, {"months": months, "data": data.model_dump(), "missed": missed})
    assert not missed, f"놓친 메뉴 {missed}"
