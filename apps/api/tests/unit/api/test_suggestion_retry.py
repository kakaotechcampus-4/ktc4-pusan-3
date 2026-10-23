"""05 다시 시도 보관함 (#249). 실패 맥락 15분 · 0개 기록 24시간 · 주인 확인 · 사용 중 표시."""

import asyncio
import time
import uuid
from datetime import UTC, datetime

import pytest

from app.api.runs import suggestion_retry as store

P, C = uuid.uuid4(), uuid.uuid4()
TASK = store.RetryTask(
    "meal_recommendation", ("내일 아침 추천해줘",), datetime(2026, 1, 1, tzinfo=UTC)
)


@pytest.fixture(autouse=True)
def _clean():
    store.clear()
    yield
    store.clear()


def _put(now=0.0, agent="food"):
    store.put_failed(run_id="r", parent_id=P, child_id=C, agent=agent, task=TASK, now=now)


def _claim(now=1.0, parent=P, child=C, agent="food"):
    return store.claim(run_id="r", parent_id=parent, child_id=child, agent=agent, now=now)


async def test_맥락을_꺼낸다():
    _put()
    assert _claim() == TASK


async def test_다른_보호자_다른_아이_다른_Agent_는_없다():
    _put()
    assert _claim(parent=uuid.uuid4()) is None
    assert _claim(child=uuid.uuid4()) is None
    assert _claim(agent="activity") is None


async def test_15분이_지나면_없다():
    _put(now=0.0)
    assert _claim(now=store.FAILED_TTL_SECONDS + 1) is None


async def test_사용_중이면_두_번째는_busy():
    _put()
    assert _claim() == TASK
    assert _claim() == store.BUSY


async def test_실패하면_사용_중만_풀린다():
    _put()
    _claim()
    store.release(run_id="r", agent="food")
    assert _claim() == TASK


async def test_실패_뒤에도_처음_시각으로_센다():
    """다시 만들기가 또 실패해 입력 run 이 아닌 곳에서 다시 넣어도 15분은 처음부터다."""
    _put(now=0.0)
    _put(now=600.0)
    assert _claim(now=store.FAILED_TTL_SECONDS + 1) is None


async def test_성공하면_지운다():
    _put()
    _claim()
    store.drop(run_id="r", agent="food")
    assert _claim() is None


async def test_0개_기록은_24시간():
    store.put_empty(
        run_id="r", parent_id=P, child_id=C, agent="activity", message="없어요", now=0.0
    )
    kwargs = {"run_id": "r", "parent_id": P, "child_id": C, "agent": "activity"}
    assert store.empty_message(**kwargs, now=store.EMPTY_TTL_SECONDS - 1) == "없어요"
    assert store.empty_message(**kwargs, now=store.EMPTY_TTL_SECONDS + 1) is None


async def test_보관_기간이_지나면_보관대에서_빠진다(monkeypatch: pytest.MonkeyPatch):
    """꺼낼 수 없는 것만으로는 부족하다 — 원문 조각이 있어 메모리에서도 지운다 (#561)."""
    monkeypatch.setattr(store, "FAILED_TTL_SECONDS", 0.05)
    _put(now=time.monotonic())

    assert ("r", "food") in store._entries

    await asyncio.sleep(0.1)

    assert ("r", "food") not in store._entries


async def test_같은_칸에_다시_넣은_항목은_앞_예약에_지워지지_않는다(
    monkeypatch: pytest.MonkeyPatch,
):
    """실패 맥락(15분)을 다시 만든 결과가 0개면 같은 칸에 0개 기록(24시간)이 들어간다."""
    monkeypatch.setattr(store, "FAILED_TTL_SECONDS", 0.05)
    _put(now=time.monotonic())
    store.put_empty(run_id="r", parent_id=P, child_id=C, agent="food", message="없어요")

    await asyncio.sleep(0.1)

    assert store.empty_message(run_id="r", parent_id=P, child_id=C, agent="food") == "없어요"


async def test_0개_기록은_맥락_꺼내기에_걸리지_않는다():
    """같은 (run, agent) 칸이라도 0개 기록은 다시 만들 맥락이 아니다."""
    store.put_empty(run_id="r", parent_id=P, child_id=C, agent="food", message="없어요", now=0.0)
    assert _claim(now=1.0) is None


async def test_실패_맥락은_0개_기록으로_읽히지_않는다():
    _put()
    assert store.empty_message(run_id="r", parent_id=P, child_id=C, agent="food", now=1.0) is None


async def test_닫힌_결과는_24시간_같은_guard_로_남는다():
    """다시 만들었더니 닫혔다 — 다음 요청도 "이어서 할 수 없어요" 대신 같은 guard (#433 ⑧)."""
    kwargs = {"run_id": "r", "parent_id": P, "child_id": C, "agent": "activity"}
    store.put_guard(
        **kwargs, code="safety_unknown", message="알레르기를 확인할 수 없어요.", now=0.0
    )

    assert store.guard_of(**kwargs, now=store.EMPTY_TTL_SECONDS - 1) == (
        "safety_unknown",
        "알레르기를 확인할 수 없어요.",
    )
    assert store.guard_of(**kwargs, now=store.EMPTY_TTL_SECONDS + 1) is None


async def test_guard_기록은_0개_기록도_맥락도_아니다():
    kwargs = {"run_id": "r", "parent_id": P, "child_id": C, "agent": "food"}
    store.put_guard(**kwargs, code="unsupported", message="m", now=0.0)

    assert store.empty_message(**kwargs, now=1.0) is None
    assert store.claim(**kwargs, now=1.0) is None


async def test_guard_기록도_주인을_본다():
    store.put_guard(
        run_id="r", parent_id=P, child_id=C, agent="food", code="unsupported", message="m", now=0.0
    )

    assert (
        store.guard_of(run_id="r", parent_id=uuid.uuid4(), child_id=C, agent="food", now=1.0)
        is None
    )
