"""한 run 에서 화면으로 가는 되묻기는 하나다 — 진짜 Food · Growth 로 본다 (#452 리뷰 · #615).

진짜 Supervisor 라우팅 · 파이프라인 · Food Agent · Growth Agent 를 쓰고, 가짜는 LLM 응답과
in-memory 저장소뿐이다. 입력 "저녁 뭐 먹일까 양치 혼자 하는 법 알려줘" 가
food:meal_recommendation + growth:routine_coaching 으로 나뉜다.

- Food: 극성을 모르는 관심 후보 하나("브로콜리", #576 기억 다리로 읽음) → 추천 3개 + 되묻기
- Growth: 양치 루틴 기록 0건 → 추천 없이 되묻기만

규칙 자체(끝난 순서와 상관없이 같은 질문 · 질문을 고르는 순서)는 `test_pipeline.py` 의 가짜
러너 테스트가 순서를 바꿔 가며 본다. 여기는 두 Agent 를 처음 같이 돌린 그 장면에서 질문이 하나만
나가고, 그것이 추천 없이 질문만 낸 Growth 의 것인지만 본다.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.agents.food.context import FoodContext
from app.agents.food.store import (
    InMemoryFoodDocs,
    InMemoryProfile,
    InMemoryRecipeCatalog,
    InMemorySafety,
    in_memory_ports,
)
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.agents.memory_bridge import StoreFoodMemory
from app.agents.pipeline import AgentQuestion, AgentResult, handle_input
from app.agents.suggestion_save import InMemorySuggestions
from tests.unit.agents.food.support import (
    BIRTH_DATE,
    MEAL_DOC,
    ScriptedLLM,
    menu_row,
    propose,
)
from tests.unit.agents.food.support import CHILD as FOOD_CHILD
from tests.unit.agents.food.support import KST as FOOD_KST
from tests.unit.agents.food.support import NOW as FOOD_NOW
from tests.unit.agents.growth.support import birth_for, fixture_docs
from tests.unit.agents.growth.support import context as growth_context
from tests.unit.agents.growth.test_growth_agent import FakeLLM as GrowthLLM
from tests.unit.agents.growth.test_growth_agent import call as growth_call
from tests.unit.agents.supervisor.test_pipeline import (
    RUN_ID,
    FakeLLM,
    _call,
    _no_commit,
    _of,
    _tools,
    memory_context,  # noqa: F401 — 픽스처
)

TEXT_FOOD = "저녁 뭐 먹일까"
TEXT_GROWTH = "양치 혼자 하는 법 알려줘"


class _Affinities:
    """`DbAffinityReader` 자리 — 극성을 모르는 food candidate 하나 (3일 전 관찰)."""

    async def affinities(self, *, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]:
        if "food" not in domains:
            return []
        return [
            AffinityRow(
                id=UUID(int=77),
                merge_key="브로콜리",
                state="candidate",
                polarity=None,
                strength=0.6,
                last_observed_on=FOOD_NOW.date() - timedelta(days=3),
                observed_days=3,
            )
        ]


def _food_context() -> FoodContext:
    memory = StoreFoodMemory(
        InMemoryStore(now=FOOD_NOW), affinities=_Affinities(), today=FOOD_NOW.date()
    )
    rows = tuple(
        menu_row(name, source="mafra_recipe", ingredients=ingredients)
        for name, ingredients in (
            ("두부조림", ("두부", "간장")),
            ("시금치나물", ("시금치", "참기름")),
            ("미역국", ("미역", "참기름")),
        )
    )
    ports = in_memory_ports(
        profile=InMemoryProfile({FOOD_CHILD: BIRTH_DATE}),
        safety=InMemorySafety([]),
        recipes=InMemoryRecipeCatalog(rows),
        docs=InMemoryFoodDocs([MEAL_DOC]),
        memory=memory,
    )
    return FoodContext(
        child_id=FOOD_CHILD, run_id=RUN_ID, now=FOOD_NOW, timezone=FOOD_KST, ports=ports
    )


def _supervisor() -> FakeLLM:
    output = {
        "segments": [
            {
                "text": TEXT_FOOD,
                "kind": "request",
                "agent": "food",
                "food_task": "meal_recommendation",
            },
            {
                "text": TEXT_GROWTH,
                "kind": "request",
                "agent": "growth",
                "growth_task": "routine_coaching",
            },
        ]
    }
    return FakeLLM(_tools(_call("s1", "route", output)))


async def test_Food_와_Growth_가_둘_다_되물어도_화면의_질문은_하나다(
    memory_context: AgentContext,  # noqa: F811
) -> None:
    events: list[Any] = []

    result = await handle_input(
        f"{TEXT_FOOD} {TEXT_GROWTH}",
        memory_context,
        {
            "food": _food_context(),
            "growth": growth_context(birth_for(30), docs=fixture_docs()),
        },
        run_id=RUN_ID,
        supervisor_client=_supervisor(),
        memory_client=FakeLLM(),
        domain_clients={
            "food": ScriptedLLM(propose("두부조림", "시금치나물", "미역국")),
            "growth": GrowthLLM(
                growth_call(
                    "propose_routine_plan",
                    {"category": "self_care", "subject": "양치", "items": []},
                )
            ),
        },
        emit=events.append,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )

    # Food 는 추천 3개에 질문이 덧붙었고 Growth 는 추천 없이 질문만 냈다 — 질문이 답의 전부인
    # Growth 의 것이 나간다. 끝나는 순서와 상관없다
    growth_outcome = next(outcome for outcome in result.domain if outcome.agent == "growth")
    asked = _of(events, AgentQuestion)
    assert [(question.agent, question.task_type) for question in asked] == [
        ("growth", "routine_coaching")
    ]
    assert asked[0].text == growth_outcome.needs_observation[0]
    # 카드는 둘 다 선다. Growth 카드는 안내 한 줄만 싣는다 — 양치 요청에 화면 반응이 남는다
    cards = {card.agent: card for card in _of(events, AgentResult)}
    assert set(cards) == {"food", "growth"}
    assert [readout.code for readout in cards["growth"].readouts] == ["empty"]
    assert len(cards["food"].suggestion_ids) == 3
