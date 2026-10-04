"""build_gate 와 mock run() 검증.

- 월령은 보호자가 입력한 생일에서 코드가 계산한다. 기준일은 KST 다.
- 동의가 없으면 health_safety 를 아예 읽지 않는다. 조회 실패는 0행과 다르다.
- 위치는 격자가 있는지만 본다. 원좌표는 context 에 없다.
"""

from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.activity.agent import run
from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.store.inmemory import InMemorySafety, in_memory_ports
from app.agents.activity.store.ports import SafetyEntry, WeatherGrid
from app.agents.common.schemas.task import DomainTask

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
# KST 9/28 01:00 = UTC 9/27 16:00. UTC 날짜로 세면 하루가 어긋나는 시간대다
NOW = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
BIRTH_36_IN_KST = date(2023, 9, 28)  # KST 로는 36개월 당일, UTC 로는 35개월
GRID = WeatherGrid(nx=98, ny=76)


def context(birth: date = BIRTH_36_IN_KST, *, grid=GRID, **ports) -> ActivityContext:
    return ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(CHILD, birth, **ports),
        grid=grid,
    )


def task(**kwargs) -> DomainTask:
    base = dict(run_id="run-1", agent="activity", task_type=None, request_texts=("주말에 뭐 하지",))
    return DomainTask(**{**base, **kwargs})


class TestBuildGate:
    async def test_월령은_KST_기준일로_센다(self):
        """UTC 로 세면 KST 00:00–09:00 사이에만 게이트가 안 열린다."""
        gate = await build_gate(context(), outdoor_ok=True)
        assert gate.stage.months == 36

    async def test_동의가_없으면_건강정보를_읽지_않는다(self):
        safety = InMemorySafety()
        gate = await build_gate(context(consent=False, safety=safety), outdoor_ok=True)
        assert safety.calls == 0
        assert gate.consent_child_health is False
        assert gate.allergy_states == ()
        assert gate.safety_ok is True  # 실패가 아니라 읽을 것이 없는 상태다

    async def test_조회_실패는_0행과_다르다(self):
        gate = await build_gate(context(safety=InMemorySafety(fail=True)), outdoor_ok=True)
        assert gate.safety_ok is False

    async def test_알레르기_행의_state_만_모은다(self):
        safety = InMemorySafety(
            [
                SafetyEntry(kind="allergy", label="밀", state="active"),
                SafetyEntry(kind="allergy", label="땅콩", state="unknown"),
                SafetyEntry(kind="environmental", label="꽃가루", state="active"),
            ]
        )
        gate = await build_gate(context(safety=safety), outdoor_ok=True)
        assert gate.allergy_states == ("active", "unknown")

    async def test_격자가_없으면_위치가_없다(self):
        gate = await build_gate(context(grid=None), outdoor_ok=True)
        assert gate.has_location is False

    async def test_야외_판정은_호출부가_넘긴_값이다(self):
        gate = await build_gate(context(), outdoor_ok=False)
        assert gate.outdoor_ok is False
        assert gate.has_location is True


class TestMockRun:
    async def test_열릴_tool_만_돌려주고_모델을_부르지_않는다(self):
        ctx = context()
        result = await run(task(), ctx)
        assert result.status == "mock"
        assert result.model_calls == 0
        assert result.stage == "preschool"
        assert result.request_texts == ("주말에 뭐 하지",)
        assert ctx.state.gate is not None

    async def test_날씨를_아직_모르면_장소_조회를_열지_않는다(self):
        """날씨 조회가 없으면 조회 실패와 같게 본다 — 실내만 (D8). 맑음으로 가정하지 않는다."""
        result = await run(task(), context())
        assert "search_nearby_places" not in result.tools
        assert "propose_activity_candidates" in result.tools

    async def test_다른_Agent_의_task_는_받지_않는다(self):
        with pytest.raises(ValueError):
            await run(task(agent="growth"), context())


class TestForTask:
    """pipeline 이 Agent 를 부르기 전에 늘 부른다. task 마다 run state 가 따로 산다 (#195 리뷰)."""

    def test_run_state_만_새로_만든다(self):
        ctx = context()
        ctx.state.seen_places["사직어린이공원"] = None  # type: ignore[assignment]
        fresh = ctx.for_task()
        assert fresh.state is not ctx.state
        assert fresh.state.seen_evidence == {}
        assert fresh.state.seen_places == {}
        assert fresh.state.suggestions == ()
        assert (fresh.child_id, fresh.run_id, fresh.ports) == (ctx.child_id, ctx.run_id, ctx.ports)

    def test_한_task_가_쓴_값이_다른_task_에_보이지_않는다(self):
        ctx = context()
        first, second = ctx.for_task(), ctx.for_task()
        first.state.seen_places["사직어린이공원"] = None  # type: ignore[assignment]
        assert second.state.seen_places == {}
