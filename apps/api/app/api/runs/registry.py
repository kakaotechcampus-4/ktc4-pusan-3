"""run 채널 — Agent 가 내보내는 진행 이벤트와, 나중에 따로 붙는 화면(SSE) 사이를 잇는 상자.

넣는 쪽(`publish`)은 sync, 꺼내는 쪽(`subscribe`)은 async 제너레이터다.

🚨 큐가 아니라 append-only 리스트다. 화면이 늦게 붙어도(POST 202 뒤 GET 을 여는 사이에
   Agent 가 벌써 Step 1·2 를 내보낸다) 두 번 붙어도(새로고침) 처음부터 전부 받는다.

🚨 프로세스 메모리에 산다. `_channels` 는 모듈 변수라 서버가 켜져 있는 동안 하나뿐이고,
   POST 가 넣은 채널을 GET 이 같은 dict 에서 찾는다. 그래서 `--workers 2` 이상이면 깨진다 —
   데모는 단일 프로세스 전제다 (#134).
"""

import asyncio
import time
import uuid

_channels: dict[str, "RunChannel"] = {}


class RunChannel:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events = []
        self.closed = False
        self.closed_at = None
        self._changed = asyncio.Event()

    def publish(self, event) -> None:
        """이벤트 하나를 붙이고 자는 구독자를 깨운다. sync 다 — pipeline 의 emit 콜백이 그렇다."""
        self.events.append(event)
        self._changed.set()

    def close(self) -> None:
        """더 올 게 없다. 구독자가 마지막까지 읽고 나서 끝나게 하고, sweep 이 치울 시각을 남긴다."""
        self.closed = True
        self.closed_at = time.monotonic()
        self._changed.set()

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
                self._changed.clear()
                await self._changed.wait()
                continue
            yield self.events[i]
            i += 1


def open_run() -> RunChannel:
    run_id = uuid.uuid4().hex
    channel = RunChannel(run_id)
    _channels[run_id] = channel
    return channel


def get(run_id: str) -> RunChannel | None:
    return _channels.get(run_id)


def sweep(ttl_seconds: float = 300.0, *, now: float | None = None) -> int:
    """닫힌 지 ttl 을 넘긴 채널을 치우고 개수를 돌려준다. 안 치우면 요청마다 메모리에 쌓인다.

    지울 것을 먼저 모으고 나서 지운다 — dict 를 돌면서 동시에 지우면 RuntimeError 다.
    """
    now = time.monotonic() if now is None else now
    expired = [
        run_id
        for run_id, channel in _channels.items()
        if channel.closed and now - channel.closed_at > ttl_seconds
    ]
    for run_id in expired:
        del _channels[run_id]
    return len(expired)


def clear() -> None:
    """전부 비운다. 테스트가 서로 새지 않게 쓰는 것이고, 서버 코드는 부르지 않는다."""
    _channels.clear()
