"""build_gate 와 mock run() 검증.

- 월령은 보호자가 입력한 생일에서 코드가 계산한다. 기준일은 KST 다.
- 동의가 없으면 health_safety 를 아예 읽지 않는다. 조회 실패는 0행과 다르다.
- 위치는 휴대폰이 흐려서 보낸 좌표다. 날씨 쪽에는 격자만 나가고, 좌표는 repr 에도 안 찍힌다.
- 알레르기 조회에 실패하면 Activity 가 닫힌다 (D7).
"""

from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.activity.agent import run
from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.readouts import READOUTS
from app.agents.activity.store.inmemory import InMemorySafety, in_memory_ports
from app.agents.activity.store.ports import CoarseLocation, SafetyEntry, WeatherGrid
from app.agents.common.schemas.task import DomainTask

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
# KST 9/28 01:00 = UTC 9/27 16:00. UTC 날짜로 세면 하루가 어긋나는 시간대다
NOW = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
BIRTH_36_IN_KST = date(2023, 9, 28)  # KST 로는 36개월 당일, UTC 로는 35개월
LOCATION = CoarseLocation(lat=35.177, lon=129.077)  # 부산광역시 대표점 → 격자 (98, 76)


def context(birth: date = BIRTH_36_IN_KST, *, location=LOCATION, **ports) -> ActivityContext:
    return ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(CHILD, birth, **ports),
        location=location,
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

    async def test_좌표가_없으면_위치가_없다(self):
        ctx = context(location=None)
        gate = await build_gate(ctx, outdoor_ok=True)
        assert gate.has_location is False
        assert ctx.grid is None

    def test_날씨_쪽에는_격자만_나간다(self):
        assert context().grid == WeatherGrid(nx=98, ny=76)

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

    async def test_날씨를_아직_모르면_실내만이다(self):
        """날씨 조회가 없으면 조회 실패와 같게 본다 — 실내만 (D8). 맑음으로 가정하지 않는다.

        장소 조회는 닫지 않는다. tool 이 실내 종류로 좁힌다.
        """
        ctx = context()
        result = await run(task(), ctx)
        assert ctx.state.gate is not None and ctx.state.gate.outdoor_ok is False
        assert "search_nearby_places" in result.tools
        assert "propose_activity_candidates" in result.tools

    async def test_다른_Agent_의_task_는_받지_않는다(self):
        with pytest.raises(ValueError):
            await run(task(agent="growth"), context())

    async def test_알레르기_조회에_실패하면_모델을_부르지_않고_닫는다(self):
        """재료 후보만 빼서는 꽃가루 · 동물털 같은 환경 알레르기를 못 막는다 (D7)."""
        result = await run(task(), context(safety=InMemorySafety(fail=True)))
        assert result.status == "blocked"
        assert result.tools == ()
        assert result.model_calls == 0
        assert [r.body for r in result.readouts] == [READOUTS.get("blocked.safety").template]
        assert result.readouts[0].body == (
            "지금 알레르기 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요."
        )
        assert result.readouts[0].authored_by == "code"

    async def test_동의가_없으면_닫지_않는다(self):
        """읽을 것이 없는 상태지 조회 실패가 아니다."""
        result = await run(task(), context(consent=False))
        assert result.status == "mock"
        assert result.readouts == ()


class TestCoarseLocation:
    def test_받은_좌표를_둘째_자리로_자른다(self):
        """클라이언트가 덜 흐려 보내도 서버에서 한 번 더 자른다. 생성자 하나라 우회할 길이 없다."""
        loc = CoarseLocation(lat=35.177019, lon=129.076952)
        assert (loc.lat, loc.lon) == (35.18, 129.08)

    def test_repr_에_좌표가_없다(self):
        """context 를 로그나 예외에 찍어도 좌표가 새지 않는다."""
        text = repr(CoarseLocation(lat=35.177019, lon=129.076952))
        assert "35" not in text
        assert "129" not in text

    def test_범위_밖이면_거절하고_값을_싣지_않는다(self):
        with pytest.raises(ValueError) as exc:
            CoarseLocation(lat=135.5, lon=129.0)
        assert "135" not in str(exc.value)
