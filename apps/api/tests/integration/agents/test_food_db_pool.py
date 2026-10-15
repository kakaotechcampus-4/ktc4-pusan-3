"""DB 어댑터로 Food 식단 추천 후보 풀을 만든다 — 모델 없이 코드만 (meal_pool.build_pool)."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agents.food.context import FoodContext
from app.agents.food.meal_pool import build_pool
from app.agents.food.store import InMemoryProfile, SafetyEntry, in_memory_ports
from app.agents.food.store.ports import MenuCatalogRow
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.food.agent_reader import DbRecipeCatalog
from app.domains.identity.models import Parent
from app.domains.suggestion.agent_reader import DbSuggestionHistory
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionKind,
    SuggestionStatus,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=KST)
BIRTH = date(2023, 2, 1)  # 44개월 — preschool
EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]


def recipe(source_id: str, name: str, *ingredients: str, codes=frozenset(), resolved=True):
    return MenuCatalogRow(
        menu_key=name,
        display_name=name,
        source="mafra_recipe",
        resolved=resolved,
        synced_at=NOW,
        ingredients=ingredients,
        allergen_codes=frozenset(codes),
        source_id=source_id,
    )


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


async def test_DB_레시피로_후보_풀이_차고_알레르기와_최근_추천은_빠진다(session, child):
    @asynccontextmanager
    async def same():
        yield session

    recipes = DbRecipeCatalog(same)
    for row in (
        recipe("1", "두부조림", "두부", "간장", codes={5, 6}),
        recipe("2", "계란말이", "달걀", "파", codes={1}),  # 달걀 알레르기
        recipe("3", "멸치볶음", "멸치", "간장", codes={5, 6}),  # 어제 추천했다
        recipe("4", "시금치나물", "시금치", "참기름", resolved=False),  # 재료 검수 전
    ):
        await recipes.put(row)
    session.add(
        Suggestion(
            child_id=child.id,
            agent=SuggestionAgent.FOOD,
            kind=SuggestionKind.GENERAL,
            content="멸치볶음",
            status=SuggestionStatus.DRAFT,
            expires_at=NOW + timedelta(hours=1),
            created_at=NOW - timedelta(days=1),
        )
    )
    await session.flush()

    context = FoodContext(
        child_id=child.id,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(
            profile=InMemoryProfile({child.id: BIRTH}),
            recipes=recipes,
            suggestions=DbSuggestionHistory(same),
        ),
    )
    context.state.safety = tuple(EGG)

    pool = await build_pool(context)

    assert [item.row.menu_key for item in pool] == ["두부조림"]
