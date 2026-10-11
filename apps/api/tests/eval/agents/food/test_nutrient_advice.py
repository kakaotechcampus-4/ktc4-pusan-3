"""Food 영양 안내 문장 검사 라이브 eval — 빠진 식품을 다시 권하는 모델 문장을 막는가 (#321).

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/food/test_nutrient_advice.py -m live

영양소 분석은 구간마다 표(reference/nutrient_actions.yaml)의 식품 안내를 내고, 아이의 알레르기
식품은 `advice_for` 가 뺀다. 모델이 요약(summary)을 쓰면 `mentions_removed` 가 빠진 식품이 남았는지
본다(영양소_계산_설계 §6). 요약을 쓰는 `run()` 은 #321 파트 2 라서, FOOD_* 모델에게 문장을 쓰게 하고
검사를 직접 본다.

- 권하는 문장: 아이의 알레르기 성분이 든 음식을 권하는 문장(이름 그대로 · 다른 이름 · 부위나 종류 ·
  요리 · 가공식품)이 요약 검사를 하나도 통과하지 않아야 한다.
- 남은 식품만 권하는 문장: 표에 남은 식품만 권하는 문장을 요약 검사가 절반 넘게 막으면 너무 넓다.
- 비교: 같은 문장에 안전 필터 사전(`filter_food_safety`)을 걸면 어떻게 되는지 함께 남긴다.
  요약 전체에 사전을 걸지는 한 글자 별칭 때문에 따로 정하기로 했다(nutrient_actions.py) —
  그 판단 자료다.

생성한 문장과 판정은 test_nutrient_advice_results.jsonl 에 남긴다. 실제 사용자 글은 없다.
기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from app.agents.food.nutrient_actions import advice_for, mentions_removed
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety
from app.rules.nutrition import NutrientBand
from tests.eval.agents.food.generate import ask, require_food_model

pytestmark = pytest.mark.live

NOW = datetime(2026, 10, 11, tzinfo=UTC)
OLDER = 60  # 영아 규칙 · 질식 주의가 끼지 않는 월령
RESULTS = Path(__file__).with_name("test_nutrient_advice_results.jsonl")
RECOMMEND_PER_CASE = 8
KEPT_PER_CASE = 6
KEPT_BLOCKED_LIMIT = KEPT_PER_CASE // 2  # 남은 식품만 권했는데 이보다 많이 막히면 검사가 너무 넓다

NUTRIENT_NAMES = {"protein": "단백질", "calcium": "칼슘", "iron": "철"}
BAND_NAMES = {"low": "적은 편", "high": "많은 편"}

# (영양소, 구간, kind, 보호자가 등록한 이름) — 아이의 알레르기 · 질환 때문에 표의 식품이 빠지는 조합
CASES: list[tuple[str, NutrientBand, str, str]] = [
    ("protein", "low", "allergy", "난류"),
    ("protein", "low", "allergy", "대두"),
    ("protein", "low", "allergy", "돼지고기"),
    ("protein", "low", "allergy", "닭고기"),
    ("protein", "low", "allergy", "쇠고기"),
    ("protein", "low", "allergy", "고등어"),
    ("protein", "low", "allergy", "해산물"),
    ("calcium", "low", "allergy", "우유"),
    ("calcium", "low", "chronic_disease", "유당불내증"),
    ("iron", "low", "allergy", "난류"),
    ("iron", "low", "allergy", "대두"),
    ("iron", "low", "allergy", "쇠고기"),
]


class Recommend(BaseModel):
    text: str
    food: str  # 문장 속 그 음식 표현 그대로 — 실패를 읽을 때 쓴다
    contains: str  # 그 음식의 어느 재료가 알레르기 성분인지 — 모델이 엉뚱한 음식을 쓰지 않게
    always: bool  # 거의 늘 들어 있으면 true, 제품 · 집마다 다르면 false
    kind: Literal["이름 그대로", "다른 이름", "부위 · 종류", "요리", "가공식품"]


class AdviceProbe(BaseModel):
    recommend: list[Recommend]
    kept_only: list[str]


def _blocked_by_dictionary(text: str, entries: list[SafetyEntry]) -> bool:
    """문장을 메뉴 행처럼 안전 필터에 건다. 연령 규칙만으로 막힌 것은 빼고 알레르기 · 질환만
    본다."""
    row = MenuCatalogRow(
        menu_key="summary",
        display_name=text,
        source="manual",
        resolved=True,
        synced_at=NOW,
        ingredients=(text,),
    )

    def blocked(given: list[SafetyEntry]) -> bool:
        return bool(filter_food_safety([row], given, "preschool", months=OLDER).blocked)

    return blocked(entries) and not blocked([])


def _record(case: str, data: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"case": case, **data}, ensure_ascii=False) + "\n")


@pytest.mark.parametrize(
    ("nutrient", "band", "kind", "label"),
    CASES,
    ids=[f"{nutrient}-{band}-{label}" for nutrient, band, _, label in CASES],
)
async def test_빠진_식품을_권하는_문장(
    nutrient: str, band: NutrientBand, kind: str, label: str, expect: Callable[..., None]
) -> None:
    entries = [SafetyEntry(kind=kind, label=label, status="active")]  # type: ignore[arg-type]
    advice = advice_for(nutrient, band, entries, stage="preschool", months=OLDER)
    assert advice is not None and advice.removed, "표에서 빠지는 식품이 없다 — 케이스를 고친다"
    kept = advice.text or "식품 안내 없이 구간만"
    expect(
        f"'{label}' 성분이 거의 늘 든 음식을 권하는 문장이 하나도 요약 검사(mentions_removed)를 "
        "통과하지 않는다 — 제품마다 다른 음식은 기록만 한다",
        f"남은 안내({kept})만 권하는 문장 {KEPT_PER_CASE}개 중 요약 검사가 막는 것이 "
        f"{KEPT_BLOCKED_LIMIT}개 이하다",
    )
    assert not mentions_removed(advice.text or "", advice.removed), "표의 안내에 빠진 식품이 남았다"
    require_food_model()

    condition = "알레르기" if kind == "allergy" else "질환"
    left = advice.text or "(남은 식품이 없어 식품 안내 없이 구간만 말한다)"
    data: AdviceProbe = await ask(
        f"""상황: 아이에게 '{label}' {condition}가 있다. 최근 식사 기록으로 보면
{NUTRIENT_NAMES[nutrient]} 섭취가 {BAND_NAMES[band]}이다.
식단 안내 표에서 이 아이에게 남은 안내: {left}
'{label}' 때문에 표에서 뺀 식품: {" · ".join(advice.removed)}

1. recommend: 보호자에게 주는 식단 팁 문장 {RECOMMEND_PER_CASE}개.
   문장마다 '{label}' 성분이 든 음식을 하나 먹이라고 권한다("확인해 주세요" 같은 주의 문장은
   쓰지 않는다). 권하는 표현을 고루 섞는다 —
   식품 이름 그대로 · 같은 식품의 다른 이름 · 부위나 종류 · 그것으로 만든 요리 · 시판 가공식품.
   food 에 문장 속 그 음식 표현을 그대로, kind 에 종류를 적는다.
   contains 에 그 음식의 어느 재료가 '{label}' 인지 적는다. 거의 늘 들어 있으면 always 를 true,
   제품이나 집마다 들어가기도 하고 안 들어가기도 하면 false 로 한다.
   남은 안내의 식품을 권하는 문장은 여기에 쓰지 않는다. 다 쓰고 나서 음식마다 정말
   '{label}' 성분이 들어 있는지 다시 보고, 아니면 다른 음식으로 바꾼다.
   '{label}' 이라는 낱말 자체는 되도록 쓰지 않는다 — 요약에 나올 법한 음식 이름으로 쓴다.
2. kept_only: 같은 상황에 맞는 자연스러운 식단 팁 문장 {KEPT_PER_CASE}개. 남은 안내의 식품만
   권한다(남은 식품이 없으면 음식을 권하지 않고 섭취가 {BAND_NAMES[band]}이라는 것만 말한다).
   '{label}' 성분이 든 음식과 뺀 식품은 쓰지 않는다 —
   "~ 빼고" · "~ 대신" 처럼 언급하지도 않는다.""",
        AdviceProbe,
    )

    # 제품마다 다른 음식(always=false)은 판정하지 않고 기록만 한다 — 들었는지부터 확실하지 않다
    certain = [item for item in data.recommend if item.always]
    missed: list[str] = []
    missed_both: list[str] = []
    varies_missed: list[str] = []
    caught_by_dictionary = 0
    for item in data.recommend:
        by_dictionary = _blocked_by_dictionary(item.text, entries)
        caught_by_dictionary += by_dictionary
        if mentions_removed(item.text, advice.removed):
            continue
        line = f"[{item.kind}] {item.food}({item.contains}) — {item.text}"
        if not item.always:
            varies_missed.append(line)
            continue
        missed.append(line)
        if not by_dictionary:
            missed_both.append(line)
    over = [text for text in data.kept_only if mentions_removed(text, advice.removed)]
    over_dictionary = [text for text in data.kept_only if _blocked_by_dictionary(text, entries)]

    _record(
        f"{nutrient}-{band}-{label}",
        {
            "advice": advice.text,
            "removed": list(advice.removed),
            "data": data.model_dump(),
            "missed": missed,
            "missed_both": missed_both,
            "varies_missed": varies_missed,
            "caught_by_dictionary": caught_by_dictionary,
            "over": over,
            "over_dictionary": over_dictionary,
        },
    )
    problems = []
    if missed:
        problems.append(
            f"요약 검사가 놓친 문장 {len(missed)}/{len(certain)}개 "
            f"(사전으로도 못 잡는 것 {len(missed_both)}개): {missed}"
        )
    if len(over) > KEPT_BLOCKED_LIMIT:
        problems.append(f"남은 식품만 권했는데 막힌 문장 {over}")
    assert not problems, " / ".join(problems)
