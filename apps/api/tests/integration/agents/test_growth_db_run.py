"""배선한 Growth 안전 포트로 교육 run 을 가짜 LLM 으로.

안전 포트는 러너 · 05 와 같은 `DbSafetyReader` 로 실제 `health_safety` 행을 읽는다.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents import entrypoint
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.agent import run
from app.agents.growth.readouts import READOUTS
from app.agents.memory.store import InMemoryStore
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.safety.agent_reader import DbSafetyReader
from app.domains.safety.models import SafetyKind
from app.domains.safety.repository import create_safety
from tests.unit.agents.growth.support import ProposesExamples

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=KST)
BIRTH = date(2023, 2, 1)  # 44개월 — 시드에 교육 [예시] 행이 있다


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=BIRTH,
        relation=ParentChildRelation.MOTHER,
    )
    return owner, child


def learning_context(child, growth_safety):
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
        growth_safety=growth_safety,
    )
    return contexts["growth"].for_task()


def learning_task() -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="growth",
        task_type="learning_suggestion",
        request_texts=("숫자 놀이 추천해줘",),
    )


async def test_실제_health_safety_행으로_교육이_열리고_표_낱말이_든_후보는_빠진다(
    session, opener, family
):
    owner, child = family
    await create_safety(
        session, child_id=child.id, parent_id=owner.id, kind=SafetyKind.ALLERGY, label="꽃가루"
    )
    llm = ProposesExamples(titles=("공원 숫자 찾기", "색깔 찾기", "모양 맞추기"))

    result = await run(learning_task(), learning_context(child, DbSafetyReader(opener)), client=llm)

    assert result.status == "completed"
    titles = [d.content.split("\n")[0] for d in result.suggestions]
    assert titles == ["색깔 찾기", "모양 맞추기"]
    assert all(r.code == "fewer" for r in result.readouts)  # 확인 문구는 없다


async def test_DB_를_못_읽으면_교육은_모델_없이_닫힌다(session, family):
    """빈 목록으로 열리면 알레르기가 있는 아이에게도 거르지 않은 활동이 나간다 (루트 §2)."""

    @asynccontextmanager
    async def broken():
        raise OSError("db down")
        yield  # pragma: no cover

    _, child = family
    llm = ProposesExamples()

    result = await run(learning_task(), learning_context(child, DbSafetyReader(broken)), client=llm)

    assert (result.status, result.model_calls, llm.seen) == ("blocked", 0, [])
    assert result.readouts == (READOUTS.render("blocked.safety"),)
