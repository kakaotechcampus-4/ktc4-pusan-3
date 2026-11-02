"""child_growth_log → Growth 성장 기록 포트 어댑터. DB 없이 도는 쪽.

safety agent_reader 테스트와 같은 모양이다.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domains.child import agent_reader
from app.domains.child.agent_reader import DbGrowthLogReader

CHILD = uuid.UUID(int=1)


def log_row(n: int, day: date, height: Decimal | None, weight: Decimal | None):
    return SimpleNamespace(
        id=uuid.UUID(int=100 + n),
        child_id=CHILD,
        measured_on=day,
        height_cm=height,
        weight_kg=weight,
        note="보호자 메모",
    )


def reader(opened: list[object] | None = None):
    @asynccontextmanager
    async def open_session():
        session = object()
        if opened is not None:
            opened.append(session)
        yield session

    return DbGrowthLogReader(open_session)


async def test_측정을_전부_Decimal_그대로_옮긴다(monkeypatch):
    calls: list[uuid.UUID] = []

    async def fake(session, *, child_id):
        calls.append(child_id)
        return [
            log_row(1, date(2026, 10, 1), Decimal("100.0"), None),
            log_row(2, date(2026, 4, 1), Decimal("95.5"), Decimal("14.2")),
        ]

    monkeypatch.setattr(agent_reader, "list_growth_logs", fake)

    rows = await reader().measurements(child_id=CHILD)

    assert calls == [CHILD]
    assert [(r.measured_on, r.height_cm, r.weight_kg) for r in rows] == [
        (date(2026, 10, 1), Decimal("100.0"), None),
        (date(2026, 4, 1), Decimal("95.5"), Decimal("14.2")),
    ]
    assert not hasattr(rows[0], "note")  # 보호자 원문 메모는 싣지 않는다


async def test_호출마다_세션을_새로_연다(monkeypatch):
    async def fake(session, *, child_id):
        return []

    monkeypatch.setattr(agent_reader, "list_growth_logs", fake)
    opened: list[object] = []
    growth = reader(opened)

    await growth.measurements(child_id=CHILD)
    await growth.measurements(child_id=CHILD)

    assert len(opened) == 2 and opened[0] is not opened[1]


async def test_읽기_실패는_빈_목록이_아니라_예외다(monkeypatch):
    async def broken(session, *, child_id):
        raise ConnectionError("db down")

    monkeypatch.setattr(agent_reader, "list_growth_logs", broken)

    with pytest.raises(ConnectionError):
        await reader().measurements(child_id=CHILD)
