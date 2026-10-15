"""배선한 Food 포트로 식단 추천 run 을 가짜 LLM 으로 끝까지 (#442 완료 기준).

DB 레시피로 추천이 나오고, 알레르기에 걸리는 레시피와 최근 추천한 메뉴는 모델에게 보이지도 않는다.
안전 포트는 러너 · 05 와 같은 `DbSafetyReader` 로 실제 `health_safety` 행을 읽는다(#460).
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.agents import entrypoint
from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.meal_pool import SAMPLE_MAX, build_pool, sample_pool
from app.agents.food.registry import closed_readout_key
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import InMemoryFoodDocs, InMemorySafety, SafetyEntry
from app.agents.food.store.ports import MenuCatalogRow
from app.agents.memory.store import InMemoryStore
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.food.agent_reader import DbMenuCatalog, DbRecipeCatalog
from app.domains.identity.models import Parent
from app.domains.safety.agent_reader import DbSafetyReader
from app.domains.safety.models import SafetyKind, SafetySeverity
from app.domains.safety.repository import create_safety
from app.domains.suggestion.agent_reader import DbSuggestionHistory
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
    SuggestionStatus,
)
from app.domains.suggestion.repository import save_agent_suggestions
from tests.unit.agents.food.support import MEAL_DOC, ScriptedLLM, propose

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=KST)
BIRTH = date(2023, 2, 1)  # 44개월 — preschool
EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]


def recipe(source_id: str, name: str, *ingredients: str, codes=()) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=name,
        display_name=name,
        source="mafra_recipe",
        resolved=True,
        synced_at=NOW,
        ingredients=ingredients,
        allergen_codes=frozenset(codes),
        source_id=source_id,
    )


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    return await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=BIRTH,
        relation=ParentChildRelation.MOTHER,
    )


def wired_contexts(child, opener, recipes, food_safety=None):
    contexts, _ = entrypoint._domain_contexts(
        child_id=child.id,
        run_id="run-1",
        now=NOW,
        store=InMemoryStore(now=NOW),
        birth_date=BIRTH,
        child_health_granted=True,
        location=None,
        activity_weather=None,
        domain_store=None,
        food_catalog=DbMenuCatalog(opener),
        food_recipes=recipes,
        food_suggestions=DbSuggestionHistory(opener),
        food_safety=food_safety,
    )
    return contexts


async def test_배선한_Food_포트로_식단_추천이_DB_레시피에서_나온다(session, opener, child):
    recipes = DbRecipeCatalog(opener)
    for row in (
        recipe("1", "두부조림", "두부", "간장", codes={5, 6}),
        recipe("2", "시금치나물", "시금치", "참기름"),
        recipe("3", "미역국", "미역", "소고기", codes={16}),
        recipe("4", "계란말이", "달걀", "파", codes={1}),  # 달걀 알레르기
        recipe("5", "멸치볶음", "멸치", "간장", codes={5, 6}),  # 어제 추천했다가 거절됐다
    ):
        await recipes.put(row)
    session.add(
        Suggestion(
            child_id=child.id,
            agent=SuggestionAgent.FOOD,
            kind=SuggestionKind.GENERAL,
            content="멸치볶음",
            status=SuggestionStatus.REJECTED,
            expires_at=NOW,
            created_at=NOW - timedelta(days=1),
        )
    )
    await session.flush()

    wired = wired_contexts(child, opener, recipes)["food"].ports
    assert wired.recipes is recipes  # 배선이 DB 어댑터를 그대로 넣었다
    # 안전 포트는 시나리오의 달걀 알레르기를 테스트가 넣는다. 문서는 가짜 추천이 인용하는
    # MEAL_DOC 하나로 바꾼다 — 시드 문서 읽기는 test_entrypoint 가 본다
    ports = replace(wired, safety=InMemorySafety(EGG), docs=InMemoryFoodDocs([MEAL_DOC]))
    context = FoodContext(child_id=child.id, run_id="run-1", now=NOW, timezone=KST, ports=ports)
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))
    task = DomainTask(
        run_id="run-1",
        agent="food",
        task_type="meal_recommendation",
        request_texts=("저녁 뭐 먹일까",),
    )

    result = await run(task, context, client=llm)

    assert result.status == "completed"
    assert [draft.content for draft in result.suggestions] == ["두부조림", "시금치나물", "미역국"]
    shown = "\n".join(str(message["content"]) for message in llm.seen[0])
    assert "계란말이" not in shown  # 알레르기에 걸린 레시피는 후보로 보여 주지도 않는다
    assert "멸치볶음" not in shown  # 최근 3일 안에 추천했다


async def test_어댑터를_붙여도_안전_포트가_없으면_Food_는_닫힌다(session, opener, child):
    """안전 어댑터를 안 넘긴 호출(테스트 · eval)은 후보가 있어도 모델을 부르지 않는다."""
    recipes = DbRecipeCatalog(opener)
    await recipes.put(recipe("1", "두부조림", "두부", "간장", codes={5, 6}))

    context = wired_contexts(child, opener, recipes)["food"]

    gate = await build_gate(context)
    assert gate.safety_ok is False
    assert closed_readout_key(FoodTaskType.MEAL_RECOMMENDATION, gate) == "blocked.safety"
    llm = ScriptedLLM()
    task = DomainTask(
        run_id="run-1",
        agent="food",
        task_type="meal_recommendation",
        request_texts=("저녁 뭐 먹일까",),
    )
    result = await run(task, context.for_task(), client=llm)
    assert (result.status, result.model_calls, llm.calls) == ("blocked", 0, 0)


async def test_문서_행이_없어도_DB_레시피로_추천하고_저장된_추천마다_근거가_있다(
    session, opener, child
):
    """#442 — 36개월부터는 Food 문서 시드가 0행이다. 모델이 근거를 비워도 Food 는 추천을 내고,
    근거는 고른 메뉴(레시피 카탈로그 행)다. Agent 가 만든 메뉴 근거 id 를 저장층이 같은 식으로
    확인하므로, 실제로 저장해 추천마다 근거 행이 있는지까지 본다(근거 0행이면 버그 — 루트 §2)."""
    recipes = DbRecipeCatalog(opener)
    for row in (
        recipe("1", "두부조림", "두부", "간장", codes={5, 6}),
        recipe("2", "시금치나물", "시금치", "참기름"),
        recipe("3", "미역국", "미역", "소고기", codes={16}),
    ):
        await recipes.put(row)
    wired = wired_contexts(child, opener, recipes)["food"].ports
    ports = replace(wired, safety=InMemorySafety([]), docs=InMemoryFoodDocs())  # 문서 행 0
    context = FoodContext(child_id=child.id, run_id="run-1", now=NOW, timezone=KST, ports=ports)
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국", evidence=[]))
    task = DomainTask(
        run_id="run-1",
        agent="food",
        task_type="meal_recommendation",
        request_texts=("저녁 뭐 먹일까",),
    )

    result = await run(task, context, client=llm)
    assert result.status == "completed"
    assert len(result.suggestions) == 3

    ids = await save_agent_suggestions(
        session,
        child_id=child.id,
        run_id="run-1",
        payloads=[draft.to_payload() for draft in result.suggestions],
        now=NOW,
    )
    evidence = (
        await session.scalars(
            select(SuggestionEvidence).where(SuggestionEvidence.suggestion_id.in_(ids))
        )
    ).all()
    assert {row.suggestion_id for row in evidence} == set(ids)  # 근거 0행인 추천이 없다
    assert {row.source_kind for row in evidence} == {"recipe_catalog"}


def meal_task(run_id: str = "run-1") -> DomainTask:
    return DomainTask(
        run_id=run_id,
        agent="food",
        task_type="meal_recommendation",
        request_texts=("저녁 뭐 먹일까",),
    )


async def test_실제_health_safety_행으로_알레르기_레시피를_후보에서_뺀다(session, opener, child):
    """#460 — 러너 · 05 가 넘기는 DbSafetyReader 로 실제 DB 행을 읽는다."""
    owner = Parent()
    session.add(owner)
    await session.flush()
    await create_safety(
        session,
        child_id=child.id,
        parent_id=owner.id,
        kind=SafetyKind.ALLERGY,
        label="달걀",
        severity=SafetySeverity.CLASS_3,  # 알레르기는 검사 등급만 받는다(DB CHECK)
    )
    recipes = DbRecipeCatalog(opener)
    for row in (
        recipe("1", "두부조림", "두부", "간장", codes={5, 6}),
        recipe("2", "시금치나물", "시금치", "참기름"),
        recipe("3", "계란말이", "달걀", "파", codes={1}),
    ):
        await recipes.put(row)
    wired = wired_contexts(child, opener, recipes, food_safety=DbSafetyReader(opener))["food"]
    ports = replace(wired.ports, docs=InMemoryFoodDocs([MEAL_DOC]))
    context = FoodContext(child_id=child.id, run_id="run-1", now=NOW, timezone=KST, ports=ports)
    llm = ScriptedLLM(propose("두부조림", "시금치나물"))

    result = await run(meal_task(), context, client=llm)

    assert result.status == "completed"
    shown = "\n".join(str(message["content"]) for message in llm.seen[0])
    assert "계란말이" not in shown


async def test_DB_를_못_읽으면_Food_는_모델_없이_닫힌다(session, opener, child):
    """빈 목록으로 열리면 알레르기가 있는 아이에게도 거르지 않은 메뉴가 나간다(§2)."""

    @asynccontextmanager
    async def broken():
        raise OSError("db down")
        yield  # pragma: no cover

    recipes = DbRecipeCatalog(opener)
    await recipes.put(recipe("1", "두부조림", "두부", "간장", codes={5, 6}))
    context = wired_contexts(child, opener, recipes, food_safety=DbSafetyReader(broken))["food"]
    llm = ScriptedLLM()

    result = await run(meal_task(), context.for_task(), client=llm)

    assert (result.status, result.model_calls, llm.calls) == ("blocked", 0, 0)


async def test_run_이_다르면_후보_표본이_다르다(session, opener, child):
    """같은 아이 · 같은 날이어도 run 마다 다른 메뉴를 보여 준다 — 표본 seed 가 run_id 다.
    DB 풀(최대 POOL_MAX)이 표본(SAMPLE_MAX)보다 커야 다양성이 생긴다. 모델은 부르지 않는다."""
    recipes = DbRecipeCatalog(opener)
    for n in range(40):
        await recipes.put(recipe(str(n), f"메뉴{n:02d}", f"재료{n:02d}"))
    context = wired_contexts(child, opener, recipes, food_safety=DbSafetyReader(opener))["food"]
    await build_gate(context)  # 실제 어댑터로 state.safety 를 채운다

    pool = await build_pool(context)
    first = {item.row.menu_key for item in sample_pool(pool, seed="run-a")}
    second = {item.row.menu_key for item in sample_pool(pool, seed="run-b")}

    assert len(pool) > SAMPLE_MAX
    assert first != second
