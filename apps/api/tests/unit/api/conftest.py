"""DB 없이 도는 api 단위 테스트의 공통 픽스처.

되묻기 보관함(`app/api/runs/pending_reply.py`)은 표(`run_hold`, #877)라 DB 가 필요하다. 이 폴더의
테스트는 DB 없이 창구 · 러너의 순서만 보므로, 같은 시그니처 · 같은 판정의 메모리 가짜를 끼운다.
판정 자체(남의 것 · 15분 · 사용 중 · 동시 claim)는 `tests/integration/api/test_run_hold.py` 가 건다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from app.agents.entrypoint import PendingMemoryContext
from app.api import quota
from app.api.runs import pending_reply, photo_run, suggestion_retry


@dataclass
class FakeEntry:
    parent_id: uuid.UUID
    child_id: uuid.UUID
    context: PendingMemoryContext
    expires_at: datetime
    in_use: bool = False


@dataclass
class FakePendingReply:
    """`pending_reply` 의 async 함수 7개를 메모리로. `entries` 로 테스트가 속을 본다."""

    entries: dict[str, FakeEntry] = field(default_factory=dict)

    def _open(self, run_id, parent_id, child_id, now) -> FakeEntry | None:
        entry = self.entries.get(run_id)
        moment = now or datetime.now(UTC)
        if entry is None or entry.in_use or entry.expires_at <= moment:
            return None
        if (entry.parent_id, entry.child_id) != (parent_id, child_id):
            return None
        return entry

    async def put(self, *, run_id, parent_id, child_id, context, now=None) -> bool:
        moment = now or datetime.now(UTC)
        self.entries[run_id] = FakeEntry(parent_id, child_id, context, moment + pending_reply.TTL)
        return True

    async def get(self, *, run_id, parent_id, child_id, now=None):
        entry = self._open(run_id, parent_id, child_id, now)
        return None if entry is None else entry.context

    async def claim(self, *, run_id, parent_id, child_id, now=None):
        entry = self._open(run_id, parent_id, child_id, now)
        if entry is None:
            return None
        entry.in_use = True
        return entry.context

    async def release(self, run_id: str) -> None:
        if run_id in self.entries:
            self.entries[run_id].in_use = False

    async def drop(self, run_id: str) -> None:
        self.entries.pop(run_id, None)

    async def open_runs(self, *, run_ids: Sequence[str], parent_id, child_id) -> set[str]:
        return {r for r in run_ids if self._open(r, parent_id, child_id, None) is not None}


@pytest.fixture(autouse=True)
def fake_pending_reply(monkeypatch: pytest.MonkeyPatch) -> FakePendingReply:
    fake = FakePendingReply()
    for name in ("put", "get", "claim", "release", "drop", "open_runs"):
        monkeypatch.setattr(pending_reply, name, getattr(fake, name))
    return fake


@dataclass
class FakeRetryEntry:
    parent_id: uuid.UUID
    child_id: uuid.UUID
    expires_at: datetime
    task: object = None
    message: str | None = None
    guard: tuple[str, str] | None = None
    in_use: bool = False


@dataclass
class FakeSuggestionRetry:
    """`suggestion_retry` 의 async 함수를 메모리로.

    판정은 tests/integration/api/test_suggestion_retry.py 가 건다.
    """

    entries: dict[tuple[str, str], FakeRetryEntry] = field(default_factory=dict)

    def _own(self, run_id, agent, parent_id, child_id, now):
        entry = self.entries.get((run_id, agent))
        moment = now or datetime.now(UTC)
        if entry is None or entry.expires_at <= moment:
            return None
        if (entry.parent_id, entry.child_id) != (parent_id, child_id):
            return None
        return entry

    async def put_failed(self, *, run_id, parent_id, child_id, agent, task, now=None) -> None:
        moment = now or datetime.now(UTC)
        kept = self._own(run_id, agent, parent_id, child_id, moment)
        expires = (
            kept.expires_at
            if kept is not None and kept.task is not None
            else moment + suggestion_retry.FAILED_TTL
        )
        self.entries[(run_id, agent)] = FakeRetryEntry(parent_id, child_id, expires, task=task)

    async def put_empty(self, *, run_id, parent_id, child_id, agent, message, now=None) -> None:
        moment = now or datetime.now(UTC)
        self.entries[(run_id, agent)] = FakeRetryEntry(
            parent_id, child_id, moment + suggestion_retry.RECORD_TTL, message=message
        )

    async def put_guard(self, *, run_id, parent_id, child_id, agent, code, message, now=None):
        moment = now or datetime.now(UTC)
        self.entries[(run_id, agent)] = FakeRetryEntry(
            parent_id, child_id, moment + suggestion_retry.RECORD_TTL, guard=(code, message)
        )

    async def guard_of(self, *, run_id, parent_id, child_id, agent, now=None):
        entry = self._own(run_id, agent, parent_id, child_id, now)
        return None if entry is None else entry.guard

    async def empty_message(self, *, run_id, parent_id, child_id, agent, now=None):
        entry = self._own(run_id, agent, parent_id, child_id, now)
        return None if entry is None else entry.message

    async def claim(self, *, run_id, parent_id, child_id, agent, now=None):
        entry = self._own(run_id, agent, parent_id, child_id, now)
        if entry is None or entry.task is None:
            return None
        if entry.in_use:
            return suggestion_retry.BUSY
        entry.in_use = True
        return entry.task

    async def release(self, *, run_id, agent) -> None:
        if (run_id, agent) in self.entries:
            self.entries[(run_id, agent)].in_use = False

    async def drop(self, *, run_id, agent) -> None:
        self.entries.pop((run_id, agent), None)


@pytest.fixture(autouse=True)
def fake_suggestion_retry(monkeypatch: pytest.MonkeyPatch) -> FakeSuggestionRetry:
    fake = FakeSuggestionRetry()
    for name in (
        "put_failed",
        "put_empty",
        "put_guard",
        "guard_of",
        "empty_message",
        "claim",
        "release",
        "drop",
    ):
        monkeypatch.setattr(suggestion_retry, name, getattr(fake, name))
    return fake


@pytest.fixture(autouse=True)
def fake_photo_run_keep(monkeypatch: pytest.MonkeyPatch) -> list:
    """사진 run 을 표에 넣는 자리. 넣은 run 을 순서대로 적는다."""
    kept: list = []

    async def keep(run, *, now=None) -> bool:
        kept.append(run)
        return True

    monkeypatch.setattr(photo_run, "keep", keep)
    return kept


@pytest.fixture(autouse=True)
def fake_quota(monkeypatch: pytest.MonkeyPatch) -> dict:
    """하루 입력 한도(`quota.consume`)를 메모리로.

    판정은 tests/integration/api/test_rate_limits.py 가 건다.
    """
    counts: dict = {}

    async def consume(*, parent_id, today, limit, now=None) -> bool:
        used = counts.get((parent_id, today), 0)
        if used >= limit:
            return False
        counts[(parent_id, today)] = used + 1
        return True

    monkeypatch.setattr(quota, "consume", consume)
    return counts
