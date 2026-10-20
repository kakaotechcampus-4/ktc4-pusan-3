"""run 채널 — app/api/runs/registry.py

Agent 가 내보내는 진행 이벤트와, 나중에 따로 붙는 화면(SSE) 사이를 잇는 상자.
넣는 쪽(`publish`)은 sync, 꺼내는 쪽(`subscribe`)은 async 제너레이터다.

🚨 큐가 아니라 append-only 리스트다. 큐는 한 번 꺼내면 사라져서, 화면이 늦게 붙거나
   (Agent 가 이미 두 개 내보낸 뒤) 두 번 붙으면(새로고침) 빈손이 된다.
   아래 "늦게 붙기" · "두 번 붙기" 두 테스트가 그 결정을 고정한다 — 큐로 만들면 둘 다 깨진다.

이 파일은 웹 지식이 필요 없다. 이벤트는 (이름, 내용) 짝이다 — 채널은 이름으로 끝 신호를
알아본다(failed · done). 내용은 여기서 상관없어서 비워 둔다.
"""

import asyncio
import uuid

import pytest

from app.api.runs import registry

PARENT = uuid.UUID(int=1)
"""채널은 만든 보호자를 반드시 안다. 여기서는 누구인지가 중요하지 않다."""

STEP_1 = ("step", {"index": 1})
STEP_2 = ("step", {"index": 2})
DONE = ("done", {})


@pytest.fixture(autouse=True)
def _clean_registry():
    """전역 dict 가 테스트 사이에 새지 않게."""
    registry.clear()
    yield
    registry.clear()


async def collect(channel: registry.RunChannel) -> list:
    """subscribe 가 끝날 때까지 받은 것을 전부 모은다. 테스트 6개가 같은 일을 해서 뽑아 둔다."""
    return [event async for event in channel.subscribe()]


async def test_open_run_gives_distinct_ids_and_registers_them():
    a = registry.open_run(parent_id=PARENT)
    b = registry.open_run(parent_id=PARENT)

    assert a.run_id != b.run_id
    assert registry.get(a.run_id) is a
    assert registry.get(b.run_id) is b


async def test_late_subscriber_receives_everything():
    """늦게 붙기 — 이벤트가 다 나가고 채널이 닫힌 뒤에 붙어도 처음부터 전부 받는다.

    실제로는 POST 202 를 받고 화면이 GET 을 여는 사이에 Agent 가 벌써 Step 1·2 를 내보낸다.
    """
    channel = registry.open_run(parent_id=PARENT)
    channel.publish(STEP_1)
    channel.publish(STEP_2)
    channel.publish(DONE)
    channel.close()

    assert await collect(channel) == [STEP_1, STEP_2, DONE]


async def test_two_subscribers_both_receive_everything():
    """두 번 붙기 — 같은 채널을 둘이 동시에 읽어도 둘 다 전부 받는다.

    🚨 큐면 여기서 깨진다. 한쪽이 꺼낸 것을 다른 쪽은 못 본다.
    화면 새로고침, 또는 탭 두 개가 이 경우다.
    """
    channel = registry.open_run(parent_id=PARENT)
    channel.publish(STEP_1)
    channel.publish(DONE)
    channel.close()

    first, second = await asyncio.gather(collect(channel), collect(channel))

    assert first == [STEP_1, DONE]
    assert second == [STEP_1, DONE]


async def test_live_subscriber_wakes_on_publish_and_ends_on_close():
    """먼저 붙기 — 구독이 먼저 열리고, 그 뒤에 들어오는 이벤트를 받다가, close 되면 끝난다.

    wait_for 의 1초는 안전장치다. subscribe 가 close 를 못 알아채고 영원히 기다리면
    테스트가 멈추는 대신 TimeoutError 로 떨어진다 — "인덱스를 먼저 보고 나서 잠든다" 를 어기면
    여기서 걸린다.
    """
    channel = registry.open_run(parent_id=PARENT)
    subscriber = asyncio.create_task(collect(channel))
    await asyncio.sleep(0)  # 구독자가 먼저 잠들 기회를 준다

    channel.publish(STEP_1)
    channel.publish(STEP_2)
    channel.close()

    assert await asyncio.wait_for(subscriber, timeout=1) == [STEP_1, STEP_2]


async def test_events_after_an_end_signal_are_dropped():
    """🚨 끝 신호(failed · done)는 run 에 한 번뿐이다. 그 뒤에 오는 것은 버린다.

    pipeline 은 Failed 뒤에 Done 을 보내고, done 뒤에도 코드가 더 돌다 터질 수 있다. 끝 신호가
    두 개 쌓이면 러너의 키 해제 판단이 엇갈린다 — done 뒤에 failed 가 붙으면 저장이 끝난 run 의
    Idempotency-Key 가 지워져, 같은 키로 다시 누를 때 두 번 저장된다.
    """
    channel = registry.open_run(parent_id=PARENT)
    assert channel.ended_with is None

    channel.publish(STEP_1)
    channel.publish(DONE)
    channel.publish(("failed", {"reason": "internal_error", "raw_text": "한 줄"}))
    channel.publish(STEP_2)

    assert channel.events == [STEP_1, DONE]
    assert channel.ended_with == "done"


async def test_unknown_run_is_none():
    assert registry.get("no-such-run") is None


async def test_closed_channel_is_removed_after_the_expiry(monkeypatch: pytest.MonkeyPatch):
    """닫힌 채널은 정한 시간 동안만 남는다 — 그 안에는 새로고침이 처음부터 받고, 지나면 없다.

    🚨 안 지우면 failed 에 실린 보호자 원문이 서버를 다시 켤 때까지 메모리에 남는다 (#533).
    """
    monkeypatch.setattr(registry, "EXPIRE_AFTER_SECONDS", 0.05)
    channel = registry.open_run(parent_id=PARENT)
    channel.publish(("failed", {"reason": "internal_error", "raw_text": "한 줄"}))
    channel.close()

    assert registry.get(channel.run_id) is channel
    assert await collect(channel) == [("failed", {"reason": "internal_error", "raw_text": "한 줄"})]

    await asyncio.sleep(0.1)

    assert registry.get(channel.run_id) is None
