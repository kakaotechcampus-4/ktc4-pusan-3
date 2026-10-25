"""되묻기 맥락 보관 — run_id 로 넣고 다음 입력의 reply_to 로 꺼낸다.

프로세스 메모리다. 재시작하면 사라지고 --workers 2 이상에서는 깨진다 (registry.py 와 같은 전제).
"""

import asyncio
import uuid

import pytest

from app.agents.entrypoint import PendingMemoryContext
from app.agents.memory.schemas.task import WorkType
from app.api.runs import pending_reply

PARENT = uuid.uuid4()
CHILD = uuid.uuid4()
CONTEXT = PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)


@pytest.fixture(autouse=True)
def _clean():
    pending_reply.clear()
    yield
    pending_reply.clear()


async def test_넣은_것을_같은_보호자_같은_아이가_꺼낸다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD) == CONTEXT


async def test_한_번_꺼내면_다시_못_꺼낸다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)
    pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD) is None


async def test_다른_보호자는_꺼낼_수_없다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert pending_reply.consume(run_id="r1", parent_id=uuid.uuid4(), child_id=CHILD) is None
    # 남의 시도로 원래 맥락이 사라지지도 않는다
    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD) == CONTEXT


async def test_다른_아이의_질문에는_이어_적을_수_없다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=uuid.uuid4()) is None


async def test_없는_run_은_none():
    assert pending_reply.consume(run_id="없음", parent_id=PARENT, child_id=CHILD) is None


async def test_ttl_이_지나면_만료된다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=0.0)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD, now=901.0) is None


async def test_ttl_안이면_꺼낼_수_있다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=0.0)

    assert (
        pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD, now=899.0) == CONTEXT
    )


async def test_ttl_이_지나면_보관대에서_빠진다(monkeypatch: pytest.MonkeyPatch):
    """꺼낼 수 없는 것만으로는 부족하다 — 조각 원문이 들어 있어 메모리에서도 지운다 (#561).

    다음 입력이 없어도 지워져야 해서 다른 것을 넣지 않고 기다린다.
    """
    monkeypatch.setattr(pending_reply, "TTL_SECONDS", 0.05)
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert "r1" in pending_reply._pending

    await asyncio.sleep(0.1)

    assert "r1" not in pending_reply._pending
