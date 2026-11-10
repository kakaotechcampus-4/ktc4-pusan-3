"""05 다시 시도 보관함 — run_hold 위 (#249 · #877 2단계).

실패 맥락 15분 · 0개 / 닫힘 기록 24시간 · 주인 확인 · 사용 중 표시. 메모리 보관함이 지키던 규칙을
표가 그대로 지키는지 건다. 시각은 `now=` 로 고정한다. 값은 합성값이다 (루트 §9).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.runs import suggestion_retry as store
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

pytestmark = pytest.mark.usefixtures("pending_db")

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
TASK = store.RetryTask(
    "meal_recommendation",
    ("synthetic request",),
    datetime(2026, 1, 1, tzinfo=UTC),
    original_texts=("synthetic original",),
    prior_texts=("synthetic prior",),
)


@pytest.fixture
def owner(bearer: Bearer, cid: uuid.UUID) -> dict:
    _, me = bearer
    return {"parent_id": me, "child_id": cid}


async def _put(owner, *, at=T0, agent="food", task=TASK):
    await store.put_failed(run_id="r", agent=agent, task=task, now=at, **owner)


async def _claim(owner, *, at=T0 + timedelta(seconds=1), agent="food"):
    return await store.claim(run_id="r", agent=agent, now=at, **owner)


# --- 실패 맥락 ------------------------------------------------------------------------------


async def test_맥락을_꺼낸다(owner):
    await _put(owner)

    assert await _claim(owner) == TASK


async def test_다른_보호자_다른_아이_다른_Agent_는_없다(session: AsyncSession, owner):
    _, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=owner["child_id"])
    _, someone = await issue_bearer(session, token="test-token-someone")
    other_child = await link_child(session, parent_id=someone)
    await _put(owner)

    assert await _claim({**owner, "parent_id": other}) is None
    assert await _claim({**owner, "child_id": other_child}) is None
    assert await _claim(owner, agent="activity") is None


async def test_15분이_지나면_없다(owner):
    await _put(owner)

    assert await _claim(owner, at=T0 + timedelta(minutes=15, seconds=1)) is None


async def test_사용_중이면_두_번째는_busy(owner):
    await _put(owner)
    await _claim(owner)

    assert await _claim(owner) == store.BUSY


async def test_실패하면_사용_중만_풀린다(owner):
    await _put(owner)
    await _claim(owner)

    await store.release(run_id="r", agent="food")

    assert await _claim(owner) == TASK


async def test_실패_뒤_다시_넣어도_처음_시각으로_센다(owner):
    """입력 run 이 같은 칸에 실패 맥락을 다시 넣어도 15분이 새로 시작되지 않는다."""
    await _put(owner, at=T0)
    await _put(owner, at=T0 + timedelta(minutes=10))

    assert await _claim(owner, at=T0 + timedelta(minutes=14)) == TASK
    await store.release(run_id="r", agent="food")
    assert await _claim(owner, at=T0 + timedelta(minutes=15, seconds=1)) is None


async def test_성공하면_지운다(owner):
    await _put(owner)
    await _claim(owner)

    await store.drop(run_id="r", agent="food")

    assert await _claim(owner) is None


# --- 0개 기록 · 닫힘 기록 ---------------------------------------------------------------------


async def test_0개_기록은_24시간(owner):
    await store.put_empty(run_id="r", agent="food", message="synthetic empty", now=T0, **owner)
    read = {"run_id": "r", "agent": "food", **owner}

    assert await store.empty_message(**read, now=T0 + timedelta(hours=23)) == "synthetic empty"
    assert await store.empty_message(**read, now=T0 + timedelta(hours=24, seconds=1)) is None


async def test_0개_기록은_맥락_꺼내기에_걸리지_않는다(owner):
    await store.put_empty(run_id="r", agent="food", message="synthetic empty", now=T0, **owner)

    assert await _claim(owner) is None


async def test_실패_맥락은_0개_기록으로_읽히지_않는다(owner):
    await _put(owner)

    assert await store.empty_message(run_id="r", agent="food", now=T0, **owner) is None


async def test_실패_맥락_자리에_0개_기록을_넣으면_24시간을_새로_센다(owner):
    """05 가 다시 만든 결과가 0개면 지운 맥락 자리에 기록을 남긴다 — 15분이 아니라 24시간이다."""
    await _put(owner, at=T0)
    await store.put_empty(
        run_id="r", agent="food", message="synthetic empty", now=T0 + timedelta(minutes=1), **owner
    )

    read = {"run_id": "r", "agent": "food", **owner}
    assert await store.empty_message(**read, now=T0 + timedelta(hours=20)) == "synthetic empty"


async def test_닫힌_결과는_24시간_같은_guard_로_남는다(owner):
    await store.put_guard(
        run_id="r", agent="food", code="synthetic_code", message="synthetic", now=T0, **owner
    )
    read = {"run_id": "r", "agent": "food", **owner}

    assert await store.guard_of(**read, now=T0 + timedelta(hours=23)) == (
        "synthetic_code",
        "synthetic",
    )
    assert await store.guard_of(**read, now=T0 + timedelta(hours=24, seconds=1)) is None


async def test_guard_기록은_0개_기록도_맥락도_아니다(owner):
    await store.put_guard(
        run_id="r", agent="food", code="synthetic_code", message="synthetic", now=T0, **owner
    )

    assert await store.empty_message(run_id="r", agent="food", now=T0, **owner) is None
    assert await _claim(owner) is None


async def test_guard_기록도_주인을_본다(session: AsyncSession, owner):
    _, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=owner["child_id"])
    await store.put_guard(
        run_id="r", agent="food", code="synthetic_code", message="synthetic", now=T0, **owner
    )

    assert (
        await store.guard_of(run_id="r", agent="food", now=T0, **{**owner, "parent_id": other})
        is None
    )


async def test_지운_아이로_넣으면_예외_없이_넘어간다(session: AsyncSession, owner):
    from sqlalchemy import delete

    from app.domains.child.models import Child

    await session.execute(delete(Child).where(Child.id == owner["child_id"]))

    await _put(owner)  # 예외가 나지 않는다
    await store.put_empty(run_id="r", agent="food", message="synthetic", now=T0, **owner)


def test_프로세스_메모리_보관함이_없다():
    for gone in ("_entries", "clear", "forget_parent", "forget_child"):
        assert not hasattr(store, gone), gone


async def test_읽을_수_없는_실패_맥락은_지우고_없는_것으로_한다(
    session: AsyncSession, owner, caplog
):
    """RetryTask 모양을 바꾼 배포 뒤 옛 행 — 원문을 로그에 남기지 않고, 15분 BUSY 로 막지 않는다."""
    from sqlalchemy import select

    from app.domains.run_hold.models import HoldKind, RunHold
    from app.domains.run_hold.repository import put_hold

    await put_hold(
        session,
        kind=HoldKind.SUGGESTION_RETRY,
        key="r:food",
        payload={"type": "failed", "task": {"request_texts": "synthetic secret"}},
        ttl=store.FAILED_TTL,
        now=T0,
        **owner,
    )

    assert await _claim(owner) is None

    assert (await session.scalars(select(RunHold.key).where(RunHold.key == "r:food"))).all() == []
    assert "synthetic secret" not in caplog.text


async def test_못_읽는_실패_맥락을_지우다_실패해도_원문이_예외로_올라가지_않는다(
    session: AsyncSession, owner, caplog, monkeypatch
):
    """except 안의 지우기가 터지면 새 예외의 __context__ 로 검증 오류(원문)가 함께 찍힌다.

    #899 리뷰 🟠1.
    """
    from app.domains.run_hold.models import HoldKind
    from app.domains.run_hold.repository import put_hold

    async def broken_drop(**_) -> None:
        raise ConnectionError("synthetic")

    await put_hold(
        session,
        kind=HoldKind.SUGGESTION_RETRY,
        key="r:food",
        payload={"type": "failed", "task": {"request_texts": "synthetic secret"}},
        ttl=store.FAILED_TTL,
        now=T0,
        **owner,
    )
    monkeypatch.setattr(store, "drop", broken_drop)

    assert await _claim(owner) is None
    assert "synthetic secret" not in caplog.text
    assert "ConnectionError" in caplog.text


async def test_만료된_실패_맥락_자리에_다시_넣으면_15분을_새로_센다(owner):
    """처음 시각을 잇는 것은 살아 있는 실패 맥락일 때뿐이다 — 만료된 것을 이으면 넣자마자 없다."""
    await _put(owner, at=T0)
    await _put(owner, at=T0 + timedelta(minutes=20))

    assert await _claim(owner, at=T0 + timedelta(minutes=30)) == TASK


async def test_0개_기록에_꺼내기를_시도해도_기록은_그대로다(owner):
    """꺼내기는 실패 맥락만 건드린다. 0개 기록을 잡거나 지우면 다음 요청의 같은 안내가 사라진다."""
    await store.put_empty(run_id="r", agent="food", message="synthetic empty", now=T0, **owner)

    assert await _claim(owner) is None
    read = {"run_id": "r", "agent": "food", **owner}
    assert await store.empty_message(**read, now=T0 + timedelta(minutes=1)) == "synthetic empty"
