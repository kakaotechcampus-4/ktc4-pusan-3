"""되묻기 맥락 보관함 — run_hold 위의 async 함수 (#877 · docs/ops/stateless-v1.md 1단계).

표 판정(남의 것 · 15분 · 사용 중)은 test_run_hold.py 가 건다. 여기서는 파사드가 더하는 것만 본다.
- `PendingMemoryContext` 를 JSON 으로 넣고 꺼내도 같은 값이다 (agents 코드는 고치지 않는다)
- 지운 아이 앞으로 늦게 온 저장은 예외 없이 False 다 — run 실패 · 알림이 되지 않는다
- 함수마다 자기 세션으로 바로 commit 한다 — 프로세스 메모리 변수가 없다

`pending_db` 가 세션 공장을 테스트 연결로 바꿔 끼운다. 값은 합성값이다 (루트 §9).
"""

import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.result import DomainPending
from app.agents.common.schemas.task import DomainTask
from app.agents.entrypoint import PendingMemoryContext
from app.agents.memory.schemas.task import FoundRef, WorkType
from app.api.runs import pending_reply
from app.domains.child.models import Child
from tests.integration.api.conftest import Bearer

pytestmark = pytest.mark.usefixtures("pending_db")

SIMPLE = PendingMemoryContext("synthetic", "synthetic question", WorkType.OBSERVE)

FULL = PendingMemoryContext(
    hint_text="synthetic hint",
    question="synthetic question",
    work=WorkType.SCHEDULE,
    transcript=("q1", "a1"),
    whole=True,
    found=(FoundRef("observation_food", "1"), FoundRef("event", "2")),
    asks_temperature=True,
    resume=DomainTask(
        run_id="r-synthetic",
        agent="health",
        task_type="medication",
        request_texts=("synthetic request",),
        record_texts=("synthetic record",),
        recorded_subjects=("synthetic subject",),
        deadline=12.5,
        answered_subject="synthetic answered",
        answer_texts=("synthetic answer",),
        original_texts=("synthetic original",),
        prior_texts=("synthetic prior",),
    ),
    asked=DomainPending(
        agent="health",
        kind="medication_which",
        question="synthetic which",
        payload={
            "op": "update",
            "candidates": [{"schedule_id": "1", "title": "synthetic"}],
            "args": {"dosage_span": None, "timing_spans": ["synthetic"]},
            "same_time": True,
        },
    ),
)


async def test_칸을_모두_채운_맥락이_그대로_돌아온다(bearer: Bearer, cid: uuid.UUID):
    _, me = bearer

    assert await pending_reply.put(run_id="r1", parent_id=me, child_id=cid, context=FULL) is True

    assert await pending_reply.claim(run_id="r1", parent_id=me, child_id=cid) == FULL


async def test_넣고_답받고_풀고_다시_답받고_지운다(bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    owner = {"run_id": "r1", "parent_id": me, "child_id": cid}
    await pending_reply.put(context=SIMPLE, **owner)

    assert await pending_reply.claim(**owner) == SIMPLE
    assert await pending_reply.get(**owner) is None  # 사용 중

    await pending_reply.release("r1")
    assert await pending_reply.claim(**owner) == SIMPLE

    await pending_reply.drop("r1")
    assert await pending_reply.get(**owner) is None


async def test_open_runs_는_답할_수_있는_run_만(bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    await pending_reply.put(run_id="open", parent_id=me, child_id=cid, context=SIMPLE)
    await pending_reply.put(run_id="busy", parent_id=me, child_id=cid, context=SIMPLE)
    await pending_reply.claim(run_id="busy", parent_id=me, child_id=cid)

    opened = await pending_reply.open_runs(
        run_ids=["open", "busy", "missing"], parent_id=me, child_id=cid
    )

    assert opened == {"open"}


async def test_지운_아이로_넣으면_False_이고_예외가_없다(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """아이를 지운 뒤 그 아이로 돌던 run 이 늦게 저장하려는 경우. 원문이 남지 않는다."""
    _, me = bearer
    await session.execute(delete(Child).where(Child.id == cid))

    stored = await pending_reply.put(run_id="late", parent_id=me, child_id=cid, context=SIMPLE)

    assert stored is False


def test_프로세스_메모리_보관함이_없다():
    """워커를 늘려도 같은 답이 나오려면 모듈에 상태가 없어야 한다."""
    for gone in ("_pending", "clear", "forget_parent", "forget_child"):
        assert not hasattr(pending_reply, gone), gone


async def test_open_runs_는_턴이_없으면_빈_set(bearer: Bearer, cid: uuid.UUID):
    """대화가 없는 날의 조회 — 빈 목록으로 쿼리를 보내지 않고 빈 set 이다."""
    _, me = bearer

    assert await pending_reply.open_runs(run_ids=[], parent_id=me, child_id=cid) == set()


async def test_읽을_수_없는_맥락은_지우고_없는_것으로_한다(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID, caplog
):
    """맥락 모양을 바꾼 코드가 배포되면 옛 행을 못 읽는다 (#899 jjaa0803 리뷰 🔴).

    검증 오류의 글에는 원문 조각(input_value)이 실린다 — 로그에 남기지 않는다. "사용 중" 으로
    15분 막히지도 않게, 그 행을 지우고 "이어서 확인할 수 없어요" 와 같은 None 으로 끝낸다.
    """
    from app.domains.run_hold.models import HoldKind, RunHold
    from app.domains.run_hold.repository import put_hold

    _, me = bearer
    owner = {"parent_id": me, "child_id": cid}
    for key in ("bad-get", "bad-claim"):
        await put_hold(
            session,
            kind=HoldKind.PENDING_REPLY,
            key=key,
            payload={"hint_text": "synthetic secret", "question": "q", "work": "no-such-work"},
            ttl=pending_reply.TTL,
            **owner,
        )

    assert await pending_reply.get(run_id="bad-get", **owner) is None
    assert await pending_reply.claim(run_id="bad-claim", **owner) is None

    left = (await session.scalars(select(RunHold.key).where(RunHold.key.like("bad-%")))).all()
    assert left == []
    assert "synthetic secret" not in caplog.text
    assert "ValidationError" in caplog.text


async def test_연결을_끊은_뒤_늦게_온_보관은_남지_않는다(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """끊기 전에 시작한 run 이 끊기 뒤에 넣으려 하면 — 보호자 · 아이가 둘 다 남아 FK 가 막지 못한다
    (#877 적대 검증). retention-v1 의 "연결 끊기 때 지체 없이" 를 넣는 쪽에서 지킨다."""
    from app.api.runs import photo_run, suggestion_retry
    from app.domains.child.models import ParentChild
    from app.domains.run_hold.models import RunHold
    from app.domains.run_hold.repository import delete_link_holds
    from tests.integration.api.conftest import issue_bearer, link_child

    _, member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member, child_id=cid)
    await session.execute(
        delete(ParentChild).where(ParentChild.parent_id == member, ParentChild.child_id == cid)
    )
    await delete_link_holds(session, parent_id=member, child_id=cid)
    owner = {"parent_id": member, "child_id": cid}

    assert await pending_reply.put(run_id="late", context=SIMPLE, **owner) is False
    await suggestion_retry.put_empty(run_id="late", agent="food", message="synthetic", **owner)
    assert await photo_run.keep(photo_run.open_photo_run(run_id="late-photo", **owner)) is False

    left = await session.scalar(
        select(func.count()).select_from(RunHold).where(RunHold.parent_id == member)
    )
    assert left == 0
