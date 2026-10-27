"""health_safety → Food · Activity · Health 안전 포트 어댑터 (#405). DB 없이 도는 쪽.

DB 로 실제 행을 읽는 것은 tests/integration/test_safety_agent_reader.py 가 본다.
"""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy.exc import OperationalError

from app.agents.activity.store import ports as activity_ports
from app.agents.common.ports import SafetyLookupError
from app.agents.food.store import ports as food_ports
from app.agents.growth.store import ports as growth_ports
from app.agents.health.store import ports as health_ports
from app.domains.safety import agent_reader
from app.domains.safety.agent_reader import ACTIVITY_KINDS, GROWTH_KINDS, DbSafetyReader
from app.domains.safety.models import SafetyKind, SafetySeverity, SafetyStatus

CHILD = uuid.UUID(int=1)


def row(
    kind: SafetyKind,
    label: str,
    status: SafetyStatus = SafetyStatus.ACTIVE,
    severity: SafetySeverity | None = None,
    reactions: tuple[str, ...] = (),
    management: str | None = None,
):
    return SimpleNamespace(
        kind=kind,
        label=label,
        status=status,
        severity=severity,
        reactions=reactions,
        management=management,
    )


def reader(opened: list[object] | None = None, *, fail: Exception | None = None):
    @asynccontextmanager
    async def open_session():
        if fail is not None:
            raise fail
        session = object()
        if opened is not None:
            opened.append(session)
        yield session

    return DbSafetyReader(open_session)


class TestLiterals:
    """DB 의 kind · status 값과 포트 Literal 이 어긋나면 kind 가 늘 때 조용히 빠진다 (#405)."""

    def test_Food_포트는_DB_의_kind_status_전부다(self):
        assert set(get_args(food_ports.SafetyKind)) == {kind.value for kind in SafetyKind}
        assert set(get_args(food_ports.SafetyStatus)) == {status.value for status in SafetyStatus}

    def test_Activity_포트는_읽는_kind_와_같고_status_는_전부다(self):
        assert set(get_args(activity_ports.SafetyKind)) == {kind.value for kind in ACTIVITY_KINDS}
        assert set(get_args(activity_ports.SafetyStatus)) == {
            status.value for status in SafetyStatus
        }

    def test_Growth_포트는_읽는_kind_와_같고_status_는_전부다(self):
        assert set(get_args(growth_ports.SafetyKind)) == {kind.value for kind in GROWTH_KINDS}
        assert set(get_args(growth_ports.SafetyStatus)) == {status.value for status in SafetyStatus}


class TestFood:
    async def test_active_목록을_그대로_세_칸만_옮긴다(self, monkeypatch):
        calls: list[uuid.UUID] = []

        async def fake(session, *, child_id):
            calls.append(child_id)
            return [
                row(SafetyKind.ALLERGY, "우유"),
                row(SafetyKind.CHRONIC_DISEASE, "유당불내증"),
            ]

        monkeypatch.setattr(agent_reader, "list_active_safety", fake)

        entries = await reader().food_safety(child_id=CHILD)

        assert calls == [CHILD]
        assert entries == [
            food_ports.SafetyEntry(kind="allergy", label="우유", status="active"),
            food_ports.SafetyEntry(kind="chronic_disease", label="유당불내증", status="active"),
        ]


class TestActivity:
    async def test_allergy_environmental_만_status_와_상관없이_묻는다(self, monkeypatch):
        seen: dict = {}

        async def fake(session, *, child_id, kinds):
            seen.update(child_id=child_id, kinds=kinds)
            return [row(SafetyKind.ENVIRONMENTAL, "꽃가루", SafetyStatus.RETRACTED)]

        monkeypatch.setattr(agent_reader, "list_safety_of_kinds", fake)

        entries = await reader().activity_safety(child_id=CHILD)

        assert seen == {"child_id": CHILD, "kinds": (SafetyKind.ALLERGY, SafetyKind.ENVIRONMENTAL)}
        assert entries == [
            activity_ports.SafetyEntry(kind="environmental", label="꽃가루", status="retracted")
        ]


class TestGrowth:
    async def test_allergy_environmental_만_status_와_상관없이_묻는다(self, monkeypatch):
        seen: dict = {}

        async def fake(session, *, child_id, kinds):
            seen.update(child_id=child_id, kinds=kinds)
            return [
                row(SafetyKind.ALLERGY, "꽃가루"),
                row(SafetyKind.ENVIRONMENTAL, "고소공포", SafetyStatus.RETRACTED),
            ]

        monkeypatch.setattr(agent_reader, "list_safety_of_kinds", fake)

        entries = await reader().growth_safety(child_id=CHILD)

        assert seen == {"child_id": CHILD, "kinds": (SafetyKind.ALLERGY, SafetyKind.ENVIRONMENTAL)}
        assert entries == [
            growth_ports.SafetyEntry(kind="allergy", label="꽃가루", status="active"),
            growth_ports.SafetyEntry(kind="environmental", label="고소공포", status="retracted"),
        ]


class TestHealth:
    async def test_Health_는_active_만_kind_전부_관리_칸까지(self, monkeypatch):
        calls: list[uuid.UUID] = []

        async def fake(session, *, child_id):
            calls.append(child_id)
            return [
                row(
                    SafetyKind.ALLERGY,
                    "땅콩",
                    severity=SafetySeverity.CLASS_3,
                    reactions=("두드러기", "구토"),
                    management="에피펜",
                ),
                row(SafetyKind.CHRONIC_DISEASE, "만성 면역력 저하증"),
            ]

        monkeypatch.setattr(agent_reader, "list_active_safety", fake)

        entries = await reader().health_safety(child_id=CHILD)

        assert calls == [CHILD]
        assert entries == [
            health_ports.HealthSafetyEntry(
                kind="allergy",
                label="땅콩",
                status="active",
                severity="class_3",
                reactions=("두드러기", "구토"),
                management="에피펜",
            ),
            health_ports.HealthSafetyEntry(
                kind="chronic_disease", label="만성 면역력 저하증", status="active"
            ),
        ]

    async def test_Health_도_못_읽으면_SafetyLookupError(self):
        down = OperationalError("SELECT ...", {}, Exception("password=secret host=db"))

        with pytest.raises(SafetyLookupError):
            await reader(fail=down).health_safety(child_id=CHILD)


class TestFailure:
    @pytest.mark.parametrize("method", ["food_safety", "activity_safety", "growth_safety"])
    async def test_DB_를_못_읽으면_빈_목록이_아니라_SafetyLookupError(self, method, caplog):
        """빈 목록이면 Agent 가 알레르기 없는 아이로 열린다."""
        down = OperationalError("SELECT ...", {}, Exception("password=secret host=db"))

        with pytest.raises(SafetyLookupError):
            await getattr(reader(fail=down), method)(child_id=CHILD)
        assert "secret" not in caplog.text  # 원문 문구는 남기지 않는다

    @pytest.mark.parametrize("method", ["food_safety", "activity_safety", "growth_safety"])
    async def test_연결이_안_돼도_SafetyLookupError(self, method):
        with pytest.raises(SafetyLookupError):
            await getattr(reader(fail=ConnectionRefusedError()), method)(child_id=CHILD)

    async def test_버그는_조회_실패로_바꾸지_않는다(self, monkeypatch):
        async def broken(session, *, child_id):
            raise ValueError("버그")

        monkeypatch.setattr(agent_reader, "list_active_safety", broken)

        with pytest.raises(ValueError):
            await reader().food_safety(child_id=CHILD)


async def test_부를_때마다_세션을_새로_연다(monkeypatch):
    """run 세션을 도메인 task 들이 같이 타지 않게 (#257)."""

    async def empty(session, **kwargs):
        return []

    monkeypatch.setattr(agent_reader, "list_active_safety", empty)
    monkeypatch.setattr(agent_reader, "list_safety_of_kinds", empty)
    opened: list[object] = []
    safety = reader(opened)

    await safety.food_safety(child_id=CHILD)
    await safety.activity_safety(child_id=CHILD)
    await safety.growth_safety(child_id=CHILD)

    assert len(opened) == 3 and len({id(session) for session in opened}) == 3
