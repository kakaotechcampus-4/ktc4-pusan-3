"""run 채널 — Agent 가 내보내는 진행 이벤트와, 나중에 따로 붙는 화면(SSE) 사이를 잇는 상자.

넣는 쪽(`publish`)은 sync, 꺼내는 쪽(`subscribe`)은 async 제너레이터다.

🚨 큐가 아니라 append-only 리스트다. 화면이 늦게 붙어도(POST 202 뒤 GET 을 여는 사이에
   Agent 가 벌써 Step 1·2 를 내보낸다) 두 번 붙어도(새로고침) 처음부터 전부 받는다.

🚨 run 을 돌리는 워커의 메모리에 산다(`_channels`). 같은 워커에 붙은 화면은 여기서 바로 읽는다.
   다른 워커에 붙은 화면을 위해 내보낸 이벤트를 순서대로 표(`run_channel` · `run_event`, UNLOGGED)
   에도 쓴다 — 채널마다 쓰는 작업 하나(`_mirror`)가 줄 세워 쓰고, 다른 워커는 그 표를 짧은 주기로
   읽는다(`sse.stream_remote`). 같은 키 재생 판정도 `lookup` 으로 다른 워커의 run 을 본다
   (#877 4단계).
   `_session_factory` 가 None 이면 표에 쓰지 않는다(테스트 대부분).

이벤트는 `(이름, 내용)` 짝이다. sse.frame() 이 그대로 `event:` · `data:` 두 줄로 쓴다.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.db import background_session
from app.domains.run_channel.repository import (
    append_events,
    get_channel,
    mark_channel,
    open_channel,
)

log = logging.getLogger(__name__)

TERMINAL = frozenset({"failed", "done"})
"""끝 신호. run 하나에 한 번만 나간다 — 화면은 둘 중 하나를 받으면 읽기를 멈춘다(sse.ts)."""

EXPIRE_AFTER_SECONDS = 300.0
"""닫힌 채널을 남겨 두는 시간(초). 새로고침한 화면이 결과를 처음부터 다시 받을 수 있는 동안이다.

🚨 그 뒤에는 반드시 지운다. failed 에는 보호자가 적은 문장이 그대로 실려 있어서, 안 지우면
   서버를 다시 켤 때까지 원문이 메모리에 남는다 (#533).
"""

OPEN_TTL = timedelta(minutes=10)
"""닫히지 않은 채널을 표에 남겨 두는 시간. run 은 60초 안에 끝나므로, 이보다 오래 열려 있으면 돌리던
워커가 죽은 것이다 — 그래도 원문을 남기지 않게 지운다."""

_channels: dict[str, "RunChannel"] = {}

_session_factory: Callable[[], AsyncSession] | None = background_session
"""표 미러를 쓸 세션 공장. None 이면 미러를 끈다."""


@dataclass(frozen=True)
class RunView:
    """같은 키 재생 판정 · SSE 주인 확인에 쓰는 run 의 겉모습 — 이 워커 것이든 다른 워커 것이든."""

    run_id: str
    parent_id: uuid.UUID
    ended_with: str | None
    closed: bool


class RunChannel:
    def __init__(self, run_id: str, parent_id: uuid.UUID):
        self.run_id = run_id
        self.parent_id = parent_id
        """이 run 을 만든 보호자. GET 은 이 보호자에게만 흘린다 — 주소에 아이 id 가 없어서
        아이 소유 검사로는 못 막는다 (auth-kakao-v1 부록 A 1번)."""
        self.events = []
        self.ended_with: str | None = None
        """나간 끝 신호의 이름(failed · done). 아직 없으면 None."""
        self.memory_committed = False
        """Memory 단계의 commit 이 끝났으면 True. commit 이후에 터져도 저장된 관찰은
        되돌리지 않는다 — _guarded 가 이 값을 보고 done/failed 를 정한다."""
        self.suggestions_saved = False
        """추천 저장이 한 번이라도 commit 됐으면 True. Memory commit 과 같이 확정된 저장이다
        (공통규약 §7) — 저장된 draft 가 있는 run 을 failed 로 닫으면 다시 보낼 때 두 벌이 된다."""
        self.closed = False
        self._changed = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        """이 run 을 돌리는 백그라운드 태스크. 참조를 여기 매달아야 GC 가 도중에 거두지 않는다."""
        self._outbox: asyncio.Queue | None = None
        self._writer: asyncio.Task[None] | None = None
        """표 미러를 쓰는 작업. 참조를 매달아야 GC 가 거두지 않는다."""
        self._opened_flag: asyncio.Event | None = None

    @property
    def committed(self) -> bool:
        """확정된 저장이 있는 run 인가. 있으면 failed 로 끝내지 않고 Idempotency 키도 놓지 않는다.

        Memory commit 과 추천 저장 commit 을 한 값으로 본다.
        """
        return self.memory_committed or self.suggestions_saved

    def publish(self, event) -> None:
        """이벤트 하나를 붙이고 자는 구독자를 깨운다. sync 다 — pipeline 의 emit 콜백이 그렇다.

        🚨 끝 신호 뒤에 오는 것은 버린다. pipeline 은 Failed 뒤에 Done 을 보내고, done 뒤에도
           결과를 기록하는 코드가 더 돌다 터질 수 있다. 끝 신호가 두 개 쌓이면 러너가 "실패한 run"
           으로 보고 Idempotency-Key 를 놓아서, 같은 키로 다시 누를 때 두 번 저장된다.
           넣는 쪽(번역기 · 러너)이 여럿이라 여기 한 곳에서 막는다.
        """
        if self.ended_with is not None:
            return
        self.events.append(event)
        name, _ = event
        if name in TERMINAL:
            self.ended_with = name
        self._changed.set()
        if self._outbox is not None:
            self._outbox.put_nowait(("event", len(self.events) - 1, event))

    def close(self) -> None:
        """더 올 게 없다. 구독자가 마지막까지 읽고 나서 끝나게 하고, 지울 때를 예약한다.

        러너는 run 이 어떻게 끝나든 finally 에서 이걸 부른다 — 그래서 지우는 일을 여기 묶는다.
        주기 청소는 따로 도는 작업이 멈추면 조용히 쌓이고, cron 은 이 프로세스 메모리를 못 본다.
        """
        self.closed = True
        self._changed.set()
        if self._outbox is not None:
            self._outbox.put_nowait(("close",))
        asyncio.get_running_loop().call_later(
            EXPIRE_AFTER_SECONDS, _channels.pop, self.run_id, None
        )

    def _start_mirror(self, factory: Callable[[], AsyncSession]) -> None:
        self._outbox = asyncio.Queue()
        self._opened_flag = asyncio.Event()
        self._writer = asyncio.create_task(self._mirror(factory), name=f"run-mirror-{self.run_id}")

    async def opened(self) -> None:
        """채널 행이 표에 쓰였다(또는 쓰기를 포기했다). 창구가 202 를 보내기 전에 기다린다 —
        화면이 202 를 받자마자 다른 워커로 SSE 를 열어도 404 가 아니게 (#899 리뷰).
        미러가 꺼져 있으면 기다릴 것이 없다."""
        if self._opened_flag is not None:
            await self._opened_flag.wait()

    async def drained(self) -> None:
        """여기까지 넣은 것이 표에 다 쓰였다. 미러가 꺼져 있으면 기다릴 것이 없다."""
        if self._outbox is not None:
            await self._outbox.join()

    async def _mirror(self, factory: Callable[[], AsyncSession]) -> None:
        """채널 행을 먼저 따로 쓰고, 그 뒤 줄 선 것을 순서대로 쓴다(쌓인 만큼 한 트랜잭션).

        🚨 실패해도 run 은 모른다 — 다른 워커의 화면만 늦거나 못 받는다. 로그에는 예외 종류만
           (실패 이벤트에 원문이 있다). 채널 행을 못 만들었으면(보호자가 없다) 나머지는 버린다.
        🚨 이벤트를 못 써도 끝 신호 · 닫힘 표시는 따로 다시 쓴다 — 안 남으면 다른 워커의 화면이
           끝을 모른 채 10분(OPEN_TTL) 동안 ping 만 받는다 (#899 리뷰).
        """
        outbox = self._outbox
        try:
            await self._open_row(factory)
            broken = False
        except Exception as exc:
            broken = True
            log.warning("run %s 의 채널 행을 쓰지 못했다 — %s", self.run_id, type(exc).__name__)
        finally:
            self._opened_flag.set()
        while True:
            batch = [await outbox.get()]
            while not outbox.empty():
                batch.append(outbox.get_nowait())
            try:
                if not broken:
                    await self._write(factory, batch)
            except Exception as exc:
                log.warning(
                    "run %s 의 채널 미러를 쓰지 못했다 — %s", self.run_id, type(exc).__name__
                )
                with contextlib.suppress(Exception):
                    await self._write(factory, batch, events_too=False)
            finally:
                for _ in batch:
                    outbox.task_done()
            if any(item[0] == "close" for item in batch):
                return

    async def _open_row(self, factory: Callable[[], AsyncSession]) -> None:
        async with factory() as session:
            await open_channel(
                session,
                run_id=self.run_id,
                parent_id=self.parent_id,
                expires_at=datetime.now(UTC) + OPEN_TTL,
            )
            await session.commit()

    async def _write(
        self, factory: Callable[[], AsyncSession], batch: list[tuple], *, events_too: bool = True
    ) -> None:
        now = datetime.now(UTC)
        # ("event", seq, (이름, 내용)) → (seq, 이름, 내용). JSONB 는 \u0000 을 받지 않는다 — 뺀다
        events = [
            (item[1], item[2][0], _without_nul(item[2][1])) for item in batch if item[0] == "event"
        ]
        marks: dict = {}
        ended = next((name for _, name, _ in reversed(events) if name in TERMINAL), None)
        if ended is not None:
            marks["ended_with"] = ended
        if any(item[0] == "close" for item in batch):
            marks["closed"] = True
            marks["expires_at"] = now + timedelta(seconds=EXPIRE_AFTER_SECONDS)
        if not marks and not (events_too and events):
            return
        async with factory() as session:
            if events_too and events:
                await append_events(session, run_id=self.run_id, events=events)
            if marks:
                await mark_channel(session, run_id=self.run_id, **marks)
            await session.commit()

    async def wait_changed(self, timeout: float | None = None) -> bool:
        """깃발을 내리고, 올라갈 때까지 잔다. timeout 안에 안 올라가면 False.

        🚨 SSE 의 하트비트가 이걸 쓴다. 제너레이터(subscribe)를 wait_for 로 감싸면 시간이 지날 때
           취소 신호가 제너레이터 안으로 들어가 죽는다. 깃발 기다리기만 취소하는 건 안전하다.
        """
        self._changed.clear()
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def subscribe(self):
        """0번부터 끝까지 내준다. 늦게 붙어도, 두 번 붙어도 처음부터다 — 구독자마다 자기 인덱스를
        가진다.

        🚨 리스트를 먼저 보고 나서 잔다. 순서를 바꾸면 이미 들어와 있는 이벤트를 놓치고 영원히
        기다린다. 깨어난 뒤 바로 yield 하지 않고 처음으로 돌아가는 이유 — close() 도 깃발을 올린다.
        """
        i = 0
        while True:
            if i >= len(self.events):
                if self.closed:
                    return
                await self.wait_changed()
                continue
            yield self.events[i]
            i += 1


def _without_nul(value):
    """문자열 속 NUL 을 뺀다 — 보호자 문장에 섞여 오면 그 이벤트를 표에 못 쓴다."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _without_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_nul(v) for v in value]
    return value


def open_run(*, parent_id: uuid.UUID) -> RunChannel:
    """🚨 parent_id 는 필수다. 빠뜨리면 누구나 열 수 있는 run 이 생긴다."""
    run_id = uuid.uuid4().hex
    channel = RunChannel(run_id, parent_id)
    _channels[run_id] = channel
    if _session_factory is not None:
        channel._start_mirror(_session_factory)
    return channel


def get(run_id: str) -> RunChannel | None:
    """이 워커의 메모리 채널. 다른 워커의 run 까지 보려면 `lookup`."""
    return _channels.get(run_id)


async def lookup(run_id: str) -> RunView | None:
    """이 워커에 있으면 메모리에서, 없으면 표에서 본다. 만료됐거나 모르는 run 이면 None."""
    channel = _channels.get(run_id)
    if channel is not None:
        return RunView(run_id, channel.parent_id, channel.ended_with, channel.closed)
    if _session_factory is None:
        return None
    async with _session_factory() as session:
        row = await get_channel(session, run_id=run_id)
    if row is None:
        return None
    return RunView(run_id, row.parent_id, row.ended_with, row.closed)


def clear() -> None:
    """전부 비운다. 테스트가 서로 새지 않게 쓰는 것이고, 서버 코드는 부르지 않는다."""
    _channels.clear()
