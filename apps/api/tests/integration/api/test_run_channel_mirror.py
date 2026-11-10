"""run 채널을 워커 밖에서도 읽기 — 이벤트 표 + 짧은 주기 읽기 (#877 4단계).

run 을 돌리는 워커는 지금처럼 메모리 채널을 쓰고, 내보낸 이벤트를 순서대로 표(`run_channel` ·
`run_event`)에도 쓴다. 화면(SSE)이 다른 워커에 붙으면 표를 짧은 주기로 읽는다.
- 이벤트는 순서대로, 끝 신호와 닫힘까지 표에 남는다
- 다른 워커에서 붙어도 처음부터 끝까지 받는다 — 늦게 붙어도, 도는 중에 붙어도
- 같은 키 재생 판정(`lookup`)은 다른 워커가 돌린 run 의 끝 신호를 본다
- 남의 run 은 다른 워커에서도 없는 run 이다 (404)
- 닫힌 채널은 5분, 끝나지 않은 채널은 10분 뒤 만료 삭제가 지운다 — 실패 이벤트에 원문이 있다

이벤트를 쓰는 작업이 백그라운드에서 자기 연결로 돌아서 테스트 트랜잭션에 묶지 않는다. 실제로
커밋하는 보호자를 만들고 끝나면 지운다(채널 · 이벤트는 CASCADE). 값은 합성값이다 (루트 §9).
"""

import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps.auth import hash_token
from app.api.runs import hold_purge, registry, sse
from app.domains.identity.models import Parent
from app.domains.identity.repository import create_access, create_session
from app.domains.run_channel.models import RunChannelRow, RunEvent
from app.infra.db.session import engine

FAILED = ("failed", {"reason": "synthetic", "raw_text": "synthetic raw"})


@pytest.fixture
async def mirror(monkeypatch: pytest.MonkeyPatch):
    """미러를 실제로 커밋하는 공장으로 켠다. 보호자 둘(+ access 토큰)을 만들고 끝나면 지운다."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(registry, "_session_factory", factory)
    monkeypatch.setattr(hold_purge, "_session_factory", factory)
    registry.clear()
    made: list[tuple[uuid.UUID, str]] = []
    now = datetime.now(UTC)
    async with factory() as s:
        for _ in range(2):
            parent = Parent()
            s.add(parent)
            await s.flush()
            token = "mirror-" + secrets.token_hex(12)
            sid = await create_session(
                s,
                parent_id=parent.id,
                refresh_hash=hash_token(f"refresh:{token}"),
                refresh_expires_at=now + timedelta(days=1),
                absolute_expires_at=now + timedelta(days=1),
            )
            await create_access(
                s, session_id=sid, token_hash=hash_token(token), expires_at=now + timedelta(hours=1)
            )
            made.append((parent.id, token))
        await s.commit()
    yield made
    registry.clear()
    async with factory() as s:
        await s.execute(delete(Parent).where(Parent.id.in_([pid for pid, _ in made])))
        await s.commit()


def _forget_locally(run_id: str) -> None:
    """다른 워커인 것처럼 — 이 프로세스의 메모리 채널을 치운다."""
    registry._channels.pop(run_id, None)


def _names(frames: list[str]) -> list[str]:
    return [f.split("\n", 1)[0].removeprefix("event: ") for f in frames]


async def _read_all(run_id: str, *, timeout: float = 3.0) -> list[str]:
    frames: list[str] = []

    async def collect():
        async for frame in sse.stream_remote(run_id, poll_seconds=0.02):
            frames.append(frame)

    await asyncio.wait_for(collect(), timeout)
    return frames


async def test_이벤트를_순서대로_표에도_쓰고_끝과_닫힘을_남긴다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(("step", {"index": 1}))
    channel.publish(("note", {"text": "synthetic"}))
    channel.publish(("done", {"run_id": channel.run_id}))
    channel.close()

    await channel.drained()

    async with registry._session_factory() as s:
        rows = (
            await s.execute(
                select(RunEvent.seq, RunEvent.name)
                .where(RunEvent.run_id == channel.run_id)
                .order_by(RunEvent.seq)
            )
        ).all()
        header = await s.get(RunChannelRow, channel.run_id)
    assert [tuple(r) for r in rows] == [(0, "step"), (1, "note"), (2, "done")]
    assert (header.parent_id, header.ended_with, header.closed) == (parent, "done", True)


async def test_다른_워커에서_늦게_붙어도_처음부터_끝까지_받는다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    for event in (("step", {"index": 1}), ("step", {"index": 2}), ("done", {})):
        channel.publish(event)
    channel.close()
    await channel.drained()
    _forget_locally(channel.run_id)

    frames = await _read_all(channel.run_id)

    assert _names(frames) == ["step", "step", "done"]
    assert frames[0] == sse.frame("step", {"index": 1})


async def test_다른_워커에서_도는_중에_붙어도_새_이벤트를_받는다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(("step", {"index": 1}))
    await channel.drained()
    run_id = channel.run_id

    async def produce_later():
        await asyncio.sleep(0.1)
        channel.publish(("step", {"index": 2}))
        await asyncio.sleep(0.1)
        channel.publish(FAILED)
        channel.close()

    producer = asyncio.create_task(produce_later())
    _forget_locally(run_id)  # 구독자는 메모리 채널을 못 본다
    frames = await _read_all(run_id)
    await producer

    assert _names(frames) == ["step", "step", "failed"]


async def test_lookup_은_다른_워커가_돌린_run_의_주인과_끝_신호를_본다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(FAILED)
    channel.close()
    await channel.drained()
    _forget_locally(channel.run_id)

    view = await registry.lookup(channel.run_id)

    assert view is not None
    assert (view.parent_id, view.ended_with, view.closed) == (parent, "failed", True)
    assert await registry.lookup("no-such-run") is None


async def test_lookup_은_같은_워커면_메모리_채널을_본다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)

    view = await registry.lookup(channel.run_id)

    assert view is not None and view.parent_id == parent and view.ended_with is None


async def test_다른_워커의_SSE_도_주인에게만_흐른다(mirror, client: AsyncClient):
    (owner, owner_token), (_, other_token) = mirror
    channel = registry.open_run(parent_id=owner)
    channel.publish(("step", {"index": 1}))
    channel.publish(("done", {}))
    channel.close()
    await channel.drained()
    _forget_locally(channel.run_id)
    url = f"/api/v1/runs/{channel.run_id}/events"

    theirs = await client.get(url, headers={"Authorization": f"Bearer {other_token}"})
    mine = await client.get(url, headers={"Authorization": f"Bearer {owner_token}"})

    assert theirs.status_code == 404
    assert mine.status_code == 200
    assert "event: step" in mine.text and "event: done" in mine.text


async def test_닫힌_채널은_5분_뒤_지운다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(FAILED)
    channel.close()
    await channel.drained()

    _forget_locally(channel.run_id)
    await hold_purge.purge_once(now=datetime.now(UTC) + timedelta(minutes=4))
    assert await registry.lookup(channel.run_id) is not None  # 5분 안에는 새로고침해도 다시 받는다

    await hold_purge.purge_once(now=datetime.now(UTC) + timedelta(minutes=6))

    assert await registry.lookup(channel.run_id) is None
    async with registry._session_factory() as s:
        left = await s.scalar(
            select(func.count()).select_from(RunEvent).where(RunEvent.run_id == channel.run_id)
        )
    assert left == 0  # 실패 이벤트의 원문까지 지워졌다


async def test_끝나지_않은_채널도_10분_뒤_지운다(mirror):
    """run 을 돌리던 워커가 죽으면 닫힘이 오지 않는다. 그래도 원문을 남기지 않는다."""
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(("step", {"index": 1}))
    await channel.drained()
    _forget_locally(channel.run_id)

    await hold_purge.purge_once(now=datetime.now(UTC) + timedelta(minutes=11))

    assert await registry.lookup(channel.run_id) is None


async def test_보호자를_지우면_채널도_사라진다(mirror):
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(("done", {}))
    channel.close()
    await channel.drained()
    _forget_locally(channel.run_id)

    async with registry._session_factory() as s:
        await s.execute(delete(Parent).where(Parent.id == parent))
        await s.commit()

    assert await registry.lookup(channel.run_id) is None


async def test_미러가_꺼져_있으면_표에_쓰지_않는다(monkeypatch: pytest.MonkeyPatch):
    """테스트 대부분은 미러를 끈다(tests/conftest.py) — 메모리 채널만으로 돈다."""
    monkeypatch.setattr(registry, "_session_factory", None)
    channel = registry.open_run(parent_id=uuid.uuid4())
    channel.publish(("done", {}))
    channel.close()

    await channel.drained()  # 기다릴 것이 없다

    assert channel.events == [("done", {})]


async def test_채널_행은_opened_를_기다리면_바로_다른_워커에서_보인다(mirror):
    """창구는 202 전에 이것을 기다린다 — 화면이 202 를 받자마자 다른 워커로 SSE 를 열어도 404 가
    아니다 (#899 리뷰)."""
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)

    await channel.opened()
    _forget_locally(channel.run_id)

    view = await registry.lookup(channel.run_id)
    assert view is not None and view.parent_id == parent


async def test_원문에_NUL_이_있어도_끝_신호와_닫힘이_남는다(mirror):
    """JSONB 는 \\u0000 을 받지 않는다. 그 이벤트 하나 때문에 닫힘이 안 남으면 다른 워커의 화면이
    10분 붙잡힌다 (#899 리뷰)."""
    (parent, _), _ = mirror
    channel = registry.open_run(parent_id=parent)
    channel.publish(("failed", {"reason": "synthetic", "raw_text": "앞\x00뒤"}))
    channel.close()
    await channel.drained()
    _forget_locally(channel.run_id)

    view = await registry.lookup(channel.run_id)
    frames = await _read_all(channel.run_id)

    assert (view.ended_with, view.closed) == ("failed", True)
    assert _names(frames) == ["failed"]
    assert "앞뒤" in frames[0]


async def test_이벤트_쓰기가_실패해도_끝_신호와_닫힘은_따로_남긴다(mirror, monkeypatch):
    """DB 가 잠깐 이벤트를 못 받아도 다른 워커의 화면은 끝을 안다 — 끝 신호 이벤트는 못 받아도
    채널이 닫혀 읽기를 마친다."""
    (parent, _), _ = mirror

    async def broken_append(*args, **kwargs):
        raise ConnectionError("synthetic")

    monkeypatch.setattr(registry, "append_events", broken_append)
    channel = registry.open_run(parent_id=parent)
    channel.publish(("done", {}))
    channel.close()
    await channel.drained()
    _forget_locally(channel.run_id)

    view = await registry.lookup(channel.run_id)
    assert (view.ended_with, view.closed) == ("done", True)
    assert await _read_all(channel.run_id) == []  # 읽기는 기다리지 않고 끝난다
