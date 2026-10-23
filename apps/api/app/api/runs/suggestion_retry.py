"""05 다시 시도 보관함 — (run_id, agent) → 실패 맥락 15분 · 0개 기록 · 닫힘 기록 24시간 (#249).

입력 run 의 pipeline 이 화면에 안 나가는 이벤트(RetryContext · EmptyResult)로 내면 러너가 넣고,
05 추천 엔드포인트가 판단 순서 ② · ③ 에서 꺼낸다. "추천 있음" 은 DB(`suggestion.run_id`)로 본다.
05 가 다시 만든 결과가 0개면 0개 기록, 닫힘(blocked · unsupported)이면 guard 기록을 남긴다 —
다음 요청이 같은 모양을 재생한다.

- 프로세스 메모리다. 재시작하면 사라지고 05 는 "이어서 할 수 없다" 로 간다 — 맥락 없이 다시
  만들지 않는다. 워커가 둘이면 나뉜다 (`--workers 1` 전제 · #134).
- 실패 맥락에는 원문 조각(request_texts)이 있다. 로그를 남기지 않고, 넣을 때 지울 때를 예약한다
  (pending_reply.py 와 같다 · #561).
- 꺼낼 때 "사용 중" 표시만 한다(#246 되묻기 보관함과 같은 방식). 다시 만들기가 성공하면 지우고,
  실패하면 표시만 푼다. 15분은 처음 넣은 시각부터 센다 — 다시 넣어도 갱신하지 않는다.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal

FAILED_TTL_SECONDS = 900.0
EMPTY_TTL_SECONDS = 86_400.0
BUSY: Final = "busy"


@dataclass(frozen=True)
class RetryTask:
    task_type: str | None
    request_texts: tuple[str, ...]
    sent_at: datetime  # 입력 run 의 기준 시각. 다시 만들 때 Agent context 의 now


@dataclass(frozen=True)
class _Entry:
    parent_id: uuid.UUID
    child_id: uuid.UUID
    stored_at: float
    task: RetryTask | None = None  # 실패 맥락
    message: str | None = None  # 0개 기록의 안내 본문
    guard: tuple[str, str] | None = None  # 닫힘 기록 — (guard code, 문구)
    in_use: bool = False

    def ttl(self) -> float:
        return FAILED_TTL_SECONDS if self.task is not None else EMPTY_TTL_SECONDS


_entries: dict[tuple[str, str], _Entry] = {}


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def _owned(
    key: tuple[str, str], parent_id: uuid.UUID, child_id: uuid.UUID, moment: float
) -> _Entry | None:
    """주인 · 아이 · 만료를 한 곳에서 본다. 이유는 밖에 알리지 않는다."""
    entry = _entries.get(key)
    if entry is None or entry.parent_id != parent_id or entry.child_id != child_id:
        return None
    if moment - entry.stored_at > entry.ttl():
        return None
    return entry


def _keep(key: tuple[str, str], entry: _Entry, moment: float) -> None:
    """넣고, 그 항목의 보관 기간이 끝날 때 지우는 예약을 건다.

    실패 맥락을 다시 넣으면 처음 시각을 이어받아서 남은 시간만 기다린다.
    """
    _entries[key] = entry
    asyncio.get_running_loop().call_later(entry.stored_at + entry.ttl() - moment, _expire, key)


def _expire(key: tuple[str, str]) -> None:
    """예약한 때가 되면 그 칸의 **지금 항목**이 만료됐는지 보고 지운다.

    🚨 같은 칸에 다시 넣을 수 있다(실패 맥락 → 0개 · 닫힘 기록). 앞 예약이 돌 때 새 항목이
       들어 있으면 남은 시간만큼 다시 예약하고 지우지 않는다. 예약이 조금 일찍 돈 경우(시계
       해상도)도 같은 길이다.
    """
    entry = _entries.get(key)
    if entry is None:
        return
    left = entry.stored_at + entry.ttl() - time.monotonic()
    if left > 0:
        asyncio.get_running_loop().call_later(left, _expire, key)
        return
    del _entries[key]


def put_failed(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    task: RetryTask,
    now: float | None = None,
) -> None:
    moment = _now(now)
    key = (run_id, agent)
    kept = _owned(key, parent_id, child_id, moment)
    stored_at = kept.stored_at if kept is not None and kept.task is not None else moment
    _keep(key, _Entry(parent_id, child_id, stored_at, task=task), moment)


def put_empty(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    message: str,
    now: float | None = None,
) -> None:
    moment = _now(now)
    _keep((run_id, agent), _Entry(parent_id, child_id, moment, message=message), moment)


def put_guard(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    code: str,
    message: str,
    now: float | None = None,
) -> None:
    moment = _now(now)
    _keep((run_id, agent), _Entry(parent_id, child_id, moment, guard=(code, message)), moment)


def guard_of(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, agent: str, now: float | None = None
) -> tuple[str, str] | None:
    entry = _owned((run_id, agent), parent_id, child_id, _now(now))
    return None if entry is None else entry.guard


def empty_message(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, agent: str, now: float | None = None
) -> str | None:
    entry = _owned((run_id, agent), parent_id, child_id, _now(now))
    return None if entry is None else entry.message


def claim(
    *, run_id: str, parent_id: uuid.UUID, child_id: uuid.UUID, agent: str, now: float | None = None
) -> RetryTask | Literal["busy"] | None:
    """실패 맥락을 꺼내 "사용 중" 으로 둔다. 다른 요청이 쓰는 중이면 BUSY.

    🚨 get 과 표시 사이에 await 가 없다 — 같은 맥락으로 온 두 요청이 둘 다 다시 만들지 않게.
    """
    key = (run_id, agent)
    entry = _owned(key, parent_id, child_id, _now(now))
    if entry is None or entry.task is None:
        return None
    if entry.in_use:
        return BUSY
    _entries[key] = replace(entry, in_use=True)
    return entry.task


def release(*, run_id: str, agent: str) -> None:
    entry = _entries.get((run_id, agent))
    if entry is not None:
        _entries[(run_id, agent)] = replace(entry, in_use=False)


def drop(*, run_id: str, agent: str) -> None:
    _entries.pop((run_id, agent), None)


def clear() -> None:
    """테스트가 서로 새지 않게 쓰는 것이고, 서버 코드는 부르지 않는다."""
    _entries.clear()
