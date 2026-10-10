"""Gate 로 Growth tool 을 여닫는 registry 검증.

- `tools_for` — 라벨 × 월령. 교육 tool 만 12개월부터다.
- 닫히는 조합은 넷이다. **동의는 `growth_review` 만 닫는다** — 교육 · 루틴 · 도서는 그대로 열린다.
- 코드 tool 은 어느 라벨에서도 모델에게 보이지 않는다.
"""

import pytest

from app.agents.common.gate import DataReady
from app.agents.growth.gating import CODE_TOOL_NAMES, MIN_MONTH, opens
from app.agents.growth.readouts import READOUTS
from app.agents.growth.registry import (
    CODE_TOOLS,
    OUTPUT_TOOL,
    TASK_TOOLS,
    closed_readout_key,
    tools_for,
)
from app.agents.growth.schemas.task import GrowthTaskType, task_type_of
from tests.unit.agents.growth.support import gate, task

LEARNING = GrowthTaskType.LEARNING_SUGGESTION
ROUTINE = GrowthTaskType.ROUTINE_COACHING
BOOK = GrowthTaskType.BOOK_SUGGESTION
REVIEW = GrowthTaskType.GROWTH_REVIEW

EDUCATION_TOOLS = ("search_education_memory", "lookup_notice", "propose_learning_activity")
NOTICE = DataReady(notice=True)


class TestToolsFor:
    def test_교육은_12개월부터_열린다(self):
        assert tools_for(LEARNING, gate(11, data=NOTICE)) == ()
        assert tools_for(LEARNING, gate(12, data=NOTICE)) == (
            "search_education_memory",
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "lookup_notice",
            "propose_learning_activity",
        )

    def test_공지가_없으면_lookup_notice_만_빠진다(self):
        names = tools_for(LEARNING, gate(30))
        assert "lookup_notice" not in names
        assert "propose_learning_activity" in names  # 공지가 없다고 추천이 막히지 않는다

    def test_루틴은_0개월부터_열린다(self):
        expected = (
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "propose_routine_plan",
        )
        for months in (0, 11, 12, 23, 24, 35, 36, 60):
            assert tools_for(ROUTINE, gate(months)) == expected

    def test_도서는_교육_기억_검색만_12개월부터다(self):
        assert tools_for(BOOK, gate(8)) == ("search_affinity", "search_books", "propose_books")
        assert tools_for(BOOK, gate(12)) == (
            "search_education_memory",
            "search_affinity",
            "search_books",
            "propose_books",
        )

    def test_성장_추이는_모델에게_열리는_tool_이_없다(self):
        for months in (0, 11, 12, 36, 60):
            assert tools_for(REVIEW, gate(months)) == ()

    def test_23_24_와_35_36_은_tool_을_바꾸지_않는다(self):
        """예절 · 습관은 tool 이 아니라 카테고리 허용표가 가른다 (check_routine_category)."""
        for label in (ROUTINE, BOOK):
            assert tools_for(label, gate(23)) == tools_for(label, gate(24))
            assert tools_for(label, gate(35)) == tools_for(label, gate(36))

    @pytest.mark.parametrize("name", EDUCATION_TOOLS)
    def test_교육_tool_의_경계는_11_12_다(self, name):
        assert MIN_MONTH[name] == 12
        assert not opens(name, gate(11, data=NOTICE))
        assert opens(name, gate(12, data=NOTICE))

    def test_교육이_아닌_tool_은_0개월부터다(self):
        for name, month in MIN_MONTH.items():
            if name not in EDUCATION_TOOLS:
                assert month == 0, name

    def test_표에_없는_tool_은_열지_않는다(self):
        assert not opens("search_nothing", gate(60))


class TestClosed:
    def test_성장_추이는_동의가_없으면_닫힌다(self):
        key = closed_readout_key(REVIEW, gate(30, consent_child_health=False))
        assert key == "closed.consent"
        assert closed_readout_key(REVIEW, gate(30)) is None

    @pytest.mark.parametrize("label", [LEARNING, ROUTINE, BOOK])
    def test_동의는_성장_추이만_닫는다(self, label):
        open_gate = gate(30, consent_child_health=False)
        assert closed_readout_key(label, open_gate) is None
        assert tools_for(label, open_gate) != ()

    def test_교육은_0_11개월에_닫힌다(self):
        assert closed_readout_key(LEARNING, gate(11)) == "closed.infant_learning"
        assert closed_readout_key(LEARNING, gate(12)) is None

    def test_교육은_알레르기_조회가_실패하면_닫힌다(self):
        assert closed_readout_key(LEARNING, gate(30, safety_ok=False)) == "blocked.safety"

    def test_월령이_안전_조회보다_먼저다(self):
        """0–11개월은 안전 정보를 읽지 않아서 safety_ok 가 의미 없다."""
        assert closed_readout_key(LEARNING, gate(8, safety_ok=False)) == "closed.infant_learning"

    @pytest.mark.parametrize("label", [ROUTINE, BOOK, REVIEW])
    def test_안전_조회_실패는_교육만_닫는다(self, label):
        assert closed_readout_key(label, gate(30, safety_ok=False)) is None

    def test_도서_API_장애는_도서만_닫는다(self):
        down = gate(30, data=DataReady(book_api=False))
        assert closed_readout_key(BOOK, down) == "closed.book_api"
        assert tools_for(BOOK, down) == ()
        for label in (LEARNING, ROUTINE, REVIEW):
            assert closed_readout_key(label, down) is None

    def test_닫힌_키는_전부_정의된_문구다(self):
        keys = {
            closed_readout_key(REVIEW, gate(30, consent_child_health=False)),
            closed_readout_key(LEARNING, gate(5)),
            closed_readout_key(LEARNING, gate(30, safety_ok=False)),
            closed_readout_key(BOOK, gate(30, data=DataReady(book_api=False))),
        }
        assert {READOUTS.get(key).key for key in keys if key} == keys
        assert keys == {
            "closed.consent",
            "closed.infant_learning",
            "blocked.safety",
            "closed.book_api",
        }

    def test_닫히면_tool_도_빈_튜플이다(self):
        assert tools_for(REVIEW, gate(30, consent_child_health=False)) == ()
        assert tools_for(LEARNING, gate(30, safety_ok=False)) == ()


class TestCodeToolsHidden:
    def test_코드_tool_은_어느_라벨_월령에서도_모델에게_보이지_않는다(self):
        for label in GrowthTaskType:
            for months in range(0, 72):
                visible = set(tools_for(label, gate(months, data=NOTICE)))
                assert not visible & CODE_TOOL_NAMES, (label, months)

    def test_모델_tool_표와_코드_tool_이름은_겹치지_않는다(self):
        assert not set(MIN_MONTH) & CODE_TOOL_NAMES
        assert set(CODE_TOOLS) <= CODE_TOOL_NAMES

    def test_라벨별_tool_은_전부_표에_있다(self):
        for names in TASK_TOOLS.values():
            assert names <= set(MIN_MONTH)

    def test_출력_tool_은_열린_tool_에_들어_있다(self):
        for label, output in OUTPUT_TOOL.items():
            assert output in tools_for(label, gate(40, data=NOTICE))
        assert REVIEW not in OUTPUT_TOOL  # 성장 추이는 추천을 만들지 않는다


class TestTaskType:
    def test_라벨_문자열을_enum_으로_바꾼다(self):
        for label in GrowthTaskType:
            assert task_type_of(task(label.value)) is label

    def test_Growth_가_아니면_거부한다(self):
        from app.agents.common.schemas.task import DomainTask

        other = DomainTask(run_id="r", agent="activity", task_type=None, request_texts=("x",))
        with pytest.raises(ValueError):
            task_type_of(other)

    def test_라벨이_없으면_거부한다(self):
        with pytest.raises(ValueError):
            task_type_of(task(None))

    def test_모르는_라벨은_거부한다(self):
        with pytest.raises(ValueError):
            task_type_of(task("health_review"))
