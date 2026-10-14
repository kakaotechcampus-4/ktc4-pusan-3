"""모델 입력 검사 — 외부 LLM 으로 나가는 것에 아이 이름 · 알레르기 라벨 · 영양 수치가 없다.

가짜 LLM 이 받은 `messages` 를 호출마다 복사해 두고(`ScriptedLLM.seen`), 마지막 호출의 messages 전체
— system 프롬프트 · user 메시지(후보 목록 · 재호출 제외 목록) · 모델이 부른 tool 인자 · 모든 tool
결과 — 를 한 덩어리로 훑는다. 마지막 호출의 messages 는 앞의 호출에서 쌓인 것을 다 포함한다.

Food `run()` 은 아이 이름을 입력으로 받지 않는다(`FoodContext` 에 이름 칸이 없다). 이름이 모델에
가려면 보호자 발화에 들어 있어야 하는데 그건 Supervisor 가 나눈 요청이다 — 여기서는 프롬프트 ·
후보 목록 · tool 결과가 새로 이름을 만들어 내지 않는지만 본다. 알레르기 라벨은 다르다 —
`health_safety` 행(`state.safety`)에 있는 값이라 어디로든 샐 수 있고, 그래서 이 테스트가 막는다.
"""

import json
import logging
import re
from typing import Any

import pytest

from app.agents.food.agent import run
from app.agents.food.store import InMemoryFoodDocs, InMemoryFoodMemory, SafetyEntry
from tests.unit.agents.food.support import (
    CHILD,
    ScriptedLLM,
    affinity,
    doc_row,
    observation,
    propose,
    tool_call,
    tool_response,
)
from tests.unit.agents.food.test_food_daycare_run import daycare_context, daycare_task
from tests.unit.agents.food.test_food_meal_run import (
    BEEF,
    EGGROLL,
    EGGSOUP,
    SOUP,
    SPINACH,
    TOFU,
    meal_context,
    meal_task,
    with_pool,
)

# 아이의 알레르기 라벨(등록한 글자)과 화면에 나가는 19종 이름. 둘 다 모델 입력에 없어야 한다
EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]
ALLERGY_TEXTS = ("달걀", "난류")
CHILD_NAME = "하준"
# 숫자 뒤에 영양 단위가 붙은 표현. 프롬프트의 "수치는 쓰지 않는다" 같은 안내는 숫자가 없다
NUTRIENT_NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s*(?:mg|㎎|kcal|㎉|g\b|%)", re.IGNORECASE)


def model_input(llm: ScriptedLLM) -> str:
    """마지막 호출의 messages 전체를 한 덩어리 글로. 앞의 호출에서 쌓인 것을 다 포함한다."""
    parts: list[str] = []
    for message in llm.seen[-1]:
        parts.append(str(message.get("content") or ""))
        parts.extend(
            json.dumps(call, ensure_ascii=False) for call in message.get("tool_calls") or []
        )
    return "\n".join(parts)


def assert_clean(text: str) -> None:
    for banned in (*ALLERGY_TEXTS, CHILD_NAME):
        assert banned not in text, f"모델 입력에 {banned!r} 가 있다"
    assert NUTRIENT_NUMBER.search(text) is None, "모델 입력에 영양 수치가 있다"


async def test_식단_추천의_모델_입력에는_알레르기_이름_아이_이름_영양_수치가_없다() -> None:
    # 모델이 조회 tool 셋을 다 부르고 한 번 거절당한 뒤 고치는 길을 간다 — tool 결과 · 거절 설명까지
    egg_doc = doc_row(
        "계란 이유식",
        row_type="meal_pattern",
        months=(12, 60),
        body="삶은 달걀노른자",
        codes=frozenset({1}),
    )
    tip = doc_row(
        "채소 먼저", row_type="feeding_tip", months=(12, 60), body="채소를 먼저 보여 준다."
    )
    memory = InMemoryFoodMemory(
        {CHILD: [affinity("딸기", polarity=1)]},
        {CHILD: [observation("딸기", days_ago=2, polarity=1)]},
    )
    context = meal_context(
        TOFU,
        SPINACH,
        SOUP,
        EGGROLL,
        safety=EGG,
        memory=memory,
        docs=InMemoryFoodDocs([egg_doc, tip]),
    )
    llm = ScriptedLLM(
        tool_response(
            tool_call("m", "search_food_memory", {"keywords": []}),
            tool_call("a", "analyze_meal_records", {"period": "last_7_days"}),
            tool_call("r", "check_repeated_menus", {"period": "last_7_days"}),
        ),
        propose("없는메뉴", "두부조림", "시금치나물"),  # 거절 설명이 tool 결과로 돌아간다
        propose("두부조림", "시금치나물", "미역국"),
    )

    result = await run(meal_task(), context, client=llm)

    assert result.status == "completed" and len(result.suggestions) == 3
    assert llm.calls == 3
    assert_clean(model_input(llm))


async def test_재호출까지_간_모델_입력에도_알레르기_이름이_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL, EGGSOUP, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(
        propose("두부조림", "계란말이", "계란국"), propose("시금치나물", "미역국", "소불고기")
    )

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.model_calls == 2
    assert len(llm.seen) == 2
    for snapshot in llm.seen:
        assert_clean("\n".join(str(message.get("content") or "") for message in snapshot))
    assert_clean(model_input(llm))


async def test_급식_갱신의_모델_입력에도_알레르기_이름이_없다() -> None:
    # 급식 메뉴 계란국 이 알레르기에 걸려도 화면에는 경고가 나가지만 모델에게는 걸렸다는 사실만 간다
    context = daycare_context(safety=EGG)
    llm = ScriptedLLM(
        tool_response(tool_call("l", "lookup_daycare_menu", {"day": "today"})),
        tool_response(
            tool_call(
                "u",
                "update_daycare_meal",
                {
                    "date_span": "오늘",
                    "slot": "lunch",
                    "removed_spans": ["된장국"],
                    "added_spans": ["계란국"],
                },
            )
        ),
    )

    result = await run(daycare_task("오늘 점심에 된장국 대신 계란국 먹었대"), context, client=llm)

    assert result.status == "completed"
    assert any("알레르기와 겹쳐요" in readout.body for readout in result.readouts)  # 화면 문구
    text = model_input(llm)
    assert_clean(text)
    # 조회 결과는 모델에게 갔다 — 알레르기 이름은 없고 확인 여부만 있다. 갱신 결과는 출력 tool 이라
    # 루프가 거기서 끝나 모델에게 다시 가지 않는다
    assert '"allergy_checked": true' in text


async def test_안전_필터_사유는_로그에_남지_않는다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # SafetyVerdict.hits(menu_key → 걸린 키) 원문 · 알레르기 이름이 로그로 안 흐른다. 건수만 남는다
    caplog.set_level(logging.DEBUG)
    with_pool(monkeypatch, TOFU, EGGROLL, EGGSOUP, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(
        propose("두부조림", "계란말이", "계란국"), propose("시금치나물", "미역국", "소불고기")
    )

    await run(meal_task(), meal_context(safety=EGG), client=llm)

    for banned in ALLERGY_TEXTS:
        assert banned not in caplog.text
    assert any("removed=2" in record.getMessage() for record in caplog.records)  # 건수는 남는다


async def test_결과에도_알레르기_이름이_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 화면(SSE)으로 가는 것 — 추천 · 개수 안내 · 이유 — 에도 알레르기 라벨이 섞이지 않는다
    with_pool(monkeypatch, TOFU, EGGROLL, EGGSOUP, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(
        propose("두부조림", "계란말이", "계란국"), propose("시금치나물", "미역국", "소불고기")
    )

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    shown: list[Any] = [
        *(readout.body for readout in result.readouts),
        *(
            f"{draft.content} {draft.reason} {draft.allergens} {draft.items}"
            for draft in result.suggestions
        ),
        *result.needs_observation,
    ]
    for text in map(str, shown):
        assert "달걀" not in text
