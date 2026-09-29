"""Gate 로 Activity tool 을 여닫는 registry 검증.

- `tools_for` — 설계 문서 D2 게이팅 표의 칸마다 한 케이스. 경계 월령 17/18 · 35/36 양쪽을 덮는다.
- 장소 조회는 월령과 별개로 위치 · 날씨가 둘 다 참이어야 열린다.
- **닫힌 조합이 없다.** 어느 월령에서도 출력 tool 이 열린다.
- `execute_tool` — 허용 목록 밖 · 코드 tool · 잘못된 인자는 실행되지 않는다.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.activity.context import ActivityContext
from app.agents.activity.gating import AFFINITY_MIN_MONTH, MIN_MONTH, opens, reads_affinity
from app.agents.activity.registry import (
    CODE_TOOLS,
    OUTPUT_TOOL,
    TOOL_HANDLERS,
    TOOL_SPECS,
    execute_tool,
    tools_for,
)
from app.agents.activity.schemas.task import ActivityTaskType
from app.agents.activity.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.activity.store.inmemory import in_memory_ports
from app.agents.common.gate import Gate
from app.agents.common.tool_runtime import ErrorCode
from app.rules.age import LifeStage, stage_of

CHILD = UUID(int=1)
NOW = datetime(2026, 9, 28, tzinfo=UTC)
TASK = ActivityTaskType.ACTIVITY_RECOMMENDATION

BASE = (
    "search_activity_memory",
    "lookup_weather",
    "lookup_schedule",
    "propose_activity_candidates",
)
WITH_PLACES = (
    "search_activity_memory",
    "lookup_weather",
    "lookup_schedule",
    "search_nearby_places",
    "propose_activity_candidates",
)


def gate(months: int, **kwargs) -> Gate:
    stage = LifeStage(
        months=months, stage=stage_of(months), big="infant" if months < 12 else "toddler"
    )
    base = dict(
        stage=stage,
        consent_child_health=True,
        safety_ok=True,
        has_location=True,
        outdoor_ok=True,
    )
    return Gate(**{**base, **kwargs})


def context() -> ActivityContext:
    return ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(CHILD, date(2023, 1, 1)),
    )


class TestToolsFor:
    @pytest.mark.parametrize("months", [0, 17, 18, 35])
    def test_36개월_미만은_장소_조회가_닫힌다(self, months):
        """위치와 날씨가 다 있어도 월령이 먼저 닫는다."""
        assert tools_for(TASK, gate(months)) == BASE

    @pytest.mark.parametrize("months", [36, 71])
    def test_36개월부터_장소_조회가_열린다(self, months):
        assert tools_for(TASK, gate(months)) == WITH_PLACES

    def test_위치가_없으면_장소_조회가_닫힌다(self):
        """기본 좌표로 대체하지 않는다. 실내 전용으로 정상 동작한다 (4-3)."""
        assert tools_for(TASK, gate(40, has_location=False)) == BASE

    def test_야외가_안_되면_장소_조회가_닫힌다(self):
        """비가 오면 통째로 닫는다. 실내 장소 소스가 Kakao 키워드뿐이라서다 (3-2)."""
        assert tools_for(TASK, gate(40, outdoor_ok=False)) == BASE

    def test_안전_조회가_실패해도_tool_을_닫지_않는다(self):
        """재료를 쓰는 후보만 빼고 고지한다 (D7). 그건 출력 검증의 일이다."""
        assert tools_for(TASK, gate(40, safety_ok=False)) == WITH_PLACES

    def test_동의가_없어도_tool_을_닫지_않는다(self):
        assert tools_for(TASK, gate(40, consent_child_health=False)) == WITH_PLACES

    @pytest.mark.parametrize("months", range(0, 72))
    def test_닫힌_조합이_없다(self, months):
        """어느 월령에서도 놀이 추천은 나간다. 출력 tool 이 빠지면 버그다."""
        opened = tools_for(TASK, gate(months, has_location=False, outdoor_ok=False))
        assert OUTPUT_TOOL[TASK] in opened

    def test_순서는_정의_순서를_따른다(self):
        order = [d.name for d in TOOL_DEFINITIONS]
        opened = tools_for(TASK, gate(40))
        assert list(opened) == sorted(opened, key=order.index)


class TestGatingTable:
    def test_모든_모델_tool_에_월령이_있다(self):
        """표에 없는 tool 은 조용히 닫힌다. 새 tool 을 더하고 표를 잊으면 여기서 잡는다."""
        assert set(MIN_MONTH) == {d.name for d in TOOL_DEFINITIONS}

    def test_표에_없는_tool_은_열지_않는다(self):
        assert opens("filter_activity_safety", gate(71)) is False

    @pytest.mark.parametrize(
        ("months", "expected"),
        [(AFFINITY_MIN_MONTH - 1, False), (AFFINITY_MIN_MONTH, True)],
    )
    def test_관심_프로필은_18개월부터_읽는다(self, months, expected):
        """tool 을 닫는 값이 아니다 — 그 아래도 관찰로 개인화가 된다."""
        assert AFFINITY_MIN_MONTH == 18
        assert reads_affinity(gate(months)) is expected


class TestCodeTools:
    def test_코드_tool_은_모델에게_보이지_않는다(self):
        spec_names = {spec["function"]["name"] for spec in TOOL_SPECS}
        assert spec_names.isdisjoint(CODE_TOOLS)
        assert set(TOOL_HANDLERS).isdisjoint(CODE_TOOLS)

    def test_모델_tool_은_다섯_개다(self):
        assert [spec["function"]["name"] for spec in TOOL_SPECS] == list(WITH_PLACES)


class TestExecuteTool:
    async def test_허용_목록_밖은_실행하지_않는다(self):
        result = await execute_tool(
            "search_nearby_places", {"category": "park"}, context(), allowed=BASE
        )
        assert result.success is False
        assert result.operation is None
        assert result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED

    async def test_코드_tool_은_이름으로_불러도_실행하지_않는다(self):
        """allowed 에 이름이 섞여 들어가도 TOOL_HANDLERS 에 없어서 막힌다."""
        result = await execute_tool(
            "filter_activity_safety", {}, context(), allowed=(*WITH_PLACES, *CODE_TOOLS)
        )
        assert result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED

    async def test_닫힌_enum_밖의_검색어는_거절한다(self):
        """장소 검색어는 닫힌 값이다. 모델이 만든 문자열이 Kakao 로 가지 않는다 (D8)."""
        result = await execute_tool(
            "search_nearby_places",
            {"category": "우리동네 키즈카페"},
            context(),
            allowed=WITH_PLACES,
        )
        assert result.error["code"] == ErrorCode.INVALID_ARGS
        assert "키즈카페" not in result.error["message"]  # 값 원문은 로그로 흘러가지 않는다

    async def test_없는_인자는_거절한다(self):
        """좌표 · 반경 · 날짜는 인자가 아니다. 모델이 넣어도 받지 않는다."""
        result = await execute_tool(
            "search_nearby_places",
            {"category": "park", "radius_m": 20_000},
            context(),
            allowed=WITH_PLACES,
        )
        assert result.error["code"] == ErrorCode.INVALID_ARGS

    async def test_통과하면_핸들러까지_간다(self):
        """지금 핸들러는 mock 이다. 예외를 삼키지 않고 그대로 올린다."""
        with pytest.raises(NotImplementedError):
            await execute_tool("lookup_weather", {}, context(), allowed=BASE)
