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
from app.api.runs import pending_reply


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
