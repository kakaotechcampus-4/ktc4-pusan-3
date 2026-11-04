"""복약 API 테스트의 공통 픽스처 — 시계 · 키 저장소 정리 · 동의한 아이 (#577)."""

import pytest

from app.api import idempotency
from app.api.v1.routers import medications as medications_router

from .helpers import (
    NOW,
    TODAY,
    grant_child_health,
)


@pytest.fixture(autouse=True)
def _clean_keys():
    idempotency.clear()
    yield
    idempotency.clear()


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(medications_router, "kst_today", lambda: TODAY)
    monkeypatch.setattr(medications_router, "utc_now", lambda: NOW)


@pytest.fixture
async def consented(session, bearer, cid):
    """보호자 1 이 아이 A 에 건강정보 동의를 해 둔 상태."""
    _, parent_1 = bearer
    await grant_child_health(session, child_id=cid, parent_id=parent_1)
    return cid
