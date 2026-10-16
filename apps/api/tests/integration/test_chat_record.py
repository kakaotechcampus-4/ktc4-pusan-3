"""run 이 대화 한 턴(`chat_turn`)으로 남는 것 — app/api/runs/chat_record.py (#228).

    ① done 으로 끝난 run 은 남고, 날짜는 접수한 순간의 KST 날짜다
    ② failed 로 끝난 run 은 남지 않는다 — 다시 보내면 같은 말풍선이 두 번 생긴다
    ③ 되묻기에 답한 run 은 질문한 날에 붙는다 — 자정을 넘겨 답해도
    ④ 연결이 끊긴 보호자의 run 은 남지 않는다 — 끊기와 run 끝이 겹쳐도 (#427 과 같은 경합)
    ⑤ 러너가 예외 뒤에 직접 닫은 run 도 남는다 — `completed: false` 로

러너가 여는 짧은 세션을 테스트 세션으로 바꿔 끼운다. 끝나면 같이 롤백된다.
발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.agents.entrypoint import MemoryNote, Ref, Saved, Step
from app.api.runs import chat_record, registry, runner
from app.domains.chat.models import ChatTurn
from app.domains.chat.repository import add_turn
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent

# 10-15 15:30 UTC 는 KST 로 10-16 00:30 — UTC 날짜로 저장하면 하루 앞에 붙는다
AFTER_MIDNIGHT_KST = datetime(2026, 10, 15, 15, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _same_session(monkeypatch, session):
    @asynccontextmanager
    async def same():
        yield session

    monkeypatch.setattr(chat_record, "background_session", same)
    registry.clear()
    yield
    registry.clear()


@pytest.fixture
async def family(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    session.add(
        ParentChild(parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return parent.id, child.id


def recorder(family, channel, *, reply_to=None, accepted_at=AFTER_MIDNIGHT_KST):
    parent_id, child_id = family
    return chat_record.TurnRecorder(
        parent_id=parent_id,
        child_id=child_id,
        run_id=channel.run_id,
        raw_text="synthetic line",
        reply_to=reply_to,
        accepted_at=accepted_at,
    )


async def turns(session) -> list[ChatTurn]:
    return list((await session.scalars(select(ChatTurn))).all())


async def test_done_run_is_saved_on_the_kst_day_it_was_accepted(session, family):
    channel = registry.open_run(parent_id=family[0])
    record = recorder(family, channel)
    record.add(Step(1, 3, "입력을 살펴보고 있어요"))
    record.add(Saved((Ref("observation_food", "o1"),)))
    record.add(MemoryNote("기록했어요.", "message"))
    channel.publish(("done", {"run_id": channel.run_id, "model_calls": 1}))

    await record.save(channel)

    [turn] = await turns(session)
    assert turn.conversation_date == date(2026, 10, 16)
    assert turn.created_at == AFTER_MIDNIGHT_KST
    assert turn.raw_text == "synthetic line"
    assert turn.replies == [
        {"type": "saved", "refs": [{"kind": "observation_food", "id": "o1"}]},
        {"type": "note", "text": "기록했어요.", "kind": "message"},
        {"type": "done", "completed": True},
    ]


async def test_failed_run_is_not_saved(session, family):
    channel = registry.open_run(parent_id=family[0])
    record = recorder(family, channel)
    channel.publish(("failed", {"reason": "unparsable", "raw_text": "synthetic line"}))

    await record.save(channel)

    assert await turns(session) == []


async def test_reply_is_saved_on_the_day_of_its_question(session, family):
    parent_id, child_id = family
    await add_turn(
        session,
        parent_id=parent_id,
        child_id=child_id,
        run_id="question-run",
        conversation_date=date(2026, 10, 15),
        raw_text="synthetic question",
        reply_to=None,
        replies=[],
        created_at=datetime(2026, 10, 15, 14, 50, tzinfo=UTC),
    )
    channel = registry.open_run(parent_id=parent_id)
    record = recorder(family, channel, reply_to="question-run")
    channel.publish(("done", {"run_id": channel.run_id, "model_calls": 1}))

    await record.save(channel)

    answer = await session.scalar(select(ChatTurn).where(ChatTurn.run_id == channel.run_id))
    assert answer.conversation_date == date(2026, 10, 15)
    assert answer.reply_to == "question-run"


async def test_unlinked_parent_leaves_no_turn(session, family):
    """run 이 도는 사이에 연결을 끊었다 — 끊은 뒤에 쓴 대화가 끊긴 보호자에게 남으면 안 된다."""
    parent_id, child_id = family
    channel = registry.open_run(parent_id=parent_id)
    record = recorder(family, channel)
    channel.publish(("done", {"run_id": channel.run_id, "model_calls": 1}))
    link = await session.scalar(select(ParentChild).where(ParentChild.parent_id == parent_id))
    await session.delete(link)
    await session.flush()

    await record.save(channel)

    assert await turns(session) == []


async def test_run_closed_by_the_runner_after_a_crash_is_saved_as_incomplete(session, family):
    """저장한 뒤에 터진 run 은 러너가 done(completed: false)으로 닫는다 — 저장된 관찰이 있으니
    대화에도 남는다. 끝 신호를 job 이 아니라 러너가 붙이는 경로라 `on_end` 로 이어 본다."""
    channel = registry.open_run(parent_id=family[0])
    record = recorder(family, channel)

    async def saved_then_boom(channel):
        record.add(Saved((Ref("observation_food", "o1"),)))
        channel.memory_committed = True
        raise RuntimeError("after commit")

    await asyncio.wait_for(
        runner.start(channel, saved_then_boom, raw_text="synthetic line", on_end=record.save),
        timeout=2,
    )

    [turn] = await turns(session)
    assert turn.replies[-1] == {"type": "done", "completed": False}
