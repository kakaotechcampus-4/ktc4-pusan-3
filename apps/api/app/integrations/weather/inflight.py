"""받는 중인 조회 — 같은 조회를 동시에 부르면 하나를 결과째(실패도) 같이 기다린다 (#959 · #962).

"이번 주말" 은 토 · 일을 동시에 본다(Activity `check_weather` 를 날마다 `gather`). 잠금으로 한 번만
받으면, 첫 조회가 실패했을 때 기다리던 호출이 잠금을 넘겨받아 하나씩 다시 부른다 — 서버가 계속
느린 날 기다림이 "읽기 제한 × 호출 수" 가 된다. 여기서는 동시 호출의 기다림이 가장 긴 하나다.

- 한 호출이 취소돼도(run 20초) 같은 조회를 기다리는 다른 호출의 조회는 끊지 않는다
  (`asyncio.shield`). 기다리던 호출이 모두 끊겨도 조회는 끝까지 가서 캐시를 채운다
- 끝나면 성공 · 실패 모두 표시를 지운다 (#328 리뷰). 다음 호출은 다시 부른다
- 캐시는 두지 않는다 — 받은 것을 캐시에 넣는 데까지가 조회 함수(`fetch`)의 일이다
- 🚨 프로세스 메모리다 — api 는 --workers 1 전제 (#134)
"""

import asyncio
from collections.abc import Callable, Coroutine, Hashable
from typing import Any


class Inflight[K: Hashable, V]:
    """키마다 받는 중인 조회 하나."""

    def __init__(self) -> None:
        self._tasks: dict[K, asyncio.Task[V]] = {}

    def __len__(self) -> int:
        return len(self._tasks)

    async def run(self, key: K, fetch: Callable[[], Coroutine[Any, Any, V]]) -> V:
        """받는 중인 같은 키의 조회가 있으면 그것을, 없으면 `fetch()` 를 새로 시작해 기다린다."""
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(fetch())
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._settle(key, done))
        return await asyncio.shield(task)

    async def aclose(self) -> None:
        """받는 중인 조회를 끊고 끝날 때까지 기다린다 — 어댑터가 닫힐 때(앱 종료). 남기면 루프가
        닫힌 뒤 "Task was destroyed but it is pending" 이 남는다 (#964 리뷰)."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _settle(self, key: K, done: asyncio.Task[V]) -> None:
        if self._tasks.get(key) is done:
            del self._tasks[key]
        # 기다리던 호출이 모두 취소됐어도 실패를 여기서 읽어 "아무도 안 본 예외" 경고를
        # 남기지 않는다
        if not done.cancelled():
            done.exception()
