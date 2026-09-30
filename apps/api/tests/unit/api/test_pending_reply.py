"""되묻기 맥락 보관 — run_id 로 넣고 다음 입력의 reply_to 로 꺼낸다.

프로세스 메모리다. 재시작하면 사라지고 --workers 2 이상에서는 깨진다 (registry.py 와 같은 전제).
"""

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


def test_넣은_것을_같은_보호자_같은_아이가_꺼낸다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD) == CONTEXT


def test_한_번_꺼내면_다시_못_꺼낸다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)
    pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD) is None


def test_다른_보호자는_꺼낼_수_없다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert pending_reply.consume(run_id="r1", parent_id=uuid.uuid4(), child_id=CHILD) is None
    # 남의 시도로 원래 맥락이 사라지지도 않는다
    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD) == CONTEXT


def test_다른_아이의_질문에는_이어_적을_수_없다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=uuid.uuid4()) is None


def test_없는_run_은_none():
    assert pending_reply.consume(run_id="없음", parent_id=PARENT, child_id=CHILD) is None


def test_ttl_이_지나면_만료된다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=0.0)

    assert pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD, now=901.0) is None


def test_ttl_안이면_꺼낼_수_있다():
    pending_reply.put(run_id="r1", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=0.0)

    assert (
        pending_reply.consume(run_id="r1", parent_id=PARENT, child_id=CHILD, now=899.0) == CONTEXT
    )


def test_sweep_이_만료된_것만_치운다():
    pending_reply.put(run_id="old", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=0.0)
    pending_reply.put(run_id="new", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=800.0)

    assert pending_reply.sweep(now=901.0) == 1
    assert (
        pending_reply.consume(run_id="new", parent_id=PARENT, child_id=CHILD, now=901.0) == CONTEXT
    )


def test_새로_넣을_때_만료된_것을_치운다():
    # 조각 원문이 들어 있어 만료된 뒤 메모리에 남겨 두지 않는다. 서버가 따로 sweep 을 부르지 않는다
    pending_reply.put(run_id="old", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=0.0)
    pending_reply.put(run_id="new", parent_id=PARENT, child_id=CHILD, context=CONTEXT, now=901.0)

    assert pending_reply.sweep(now=901.0) == 0  # old 는 이미 치워졌다
