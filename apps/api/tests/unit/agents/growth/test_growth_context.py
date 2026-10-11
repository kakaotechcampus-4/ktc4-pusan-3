"""build_gate 와 mock run() 검증.

- 월령은 보호자가 입력한 생일에서 코드가 계산한다. 기준일은 KST 다.
- 건강정보는 라벨이 필요할 때만, 동의가 있을 때만 읽는다. 조회 실패는 0행과 다르다.
- 동의는 `growth_review` 만 닫는다 — 교육 · 루틴 · 도서는 동의가 없어도 돈다.
- `growth_review` 는 모델 0회로 끝까지 돈다.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from app.agents.common.schemas.task import DomainTask
from app.agents.growth.agent import run
from app.agents.growth.context import GrowthContext, build_gate
from app.agents.growth.readouts import READOUTS
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import (
    InMemoryBooks,
    InMemoryConsent,
    InMemoryGrowthLog,
    InMemoryNotice,
    InMemorySafety,
)
from app.agents.growth.store.ports import SafetyEntry
from app.agents.growth.tools.safety import SafetyRulesUnavailable
from tests.unit.agents.growth.support import (
    BIRTH_12_IN_KST,
    CHILD,
    KST,
    NOW,
    birth_for,
    context,
    measurement,
    task,
)

LEARNING = GrowthTaskType.LEARNING_SUGGESTION
ROUTINE = GrowthTaskType.ROUTINE_COACHING
BOOK = GrowthTaskType.BOOK_SUGGESTION
REVIEW = GrowthTaskType.GROWTH_REVIEW


class TestAge:
    async def test_월령은_KST_기준일로_센다(self):
        """UTC 로 세면 KST 00:00–09:00 사이에 하루가 어긋나 게이트가 안 열린다."""
        gate = await build_gate(context(BIRTH_12_IN_KST), LEARNING)
        assert gate.stage.months == 12

    async def test_생일_당일_00시_5분_KST_에_교육이_열린다(self):
        just_after_midnight = datetime(2026, 9, 27, 15, 5, tzinfo=UTC)  # KST 9/28 00:05
        just_before = datetime(2026, 9, 27, 14, 55, tzinfo=UTC)  # KST 9/27 23:55
        ctx = context(BIRTH_12_IN_KST)
        opened = await build_gate(_at(ctx, just_after_midnight), LEARNING)
        closed = await build_gate(_at(ctx, just_before), LEARNING)
        assert (opened.stage.months, closed.stage.months) == (12, 11)

    async def test_생일이_없는_아이는_조용히_넘어가지_않는다(self):
        ctx = GrowthContext(
            child_id=UUID(int=99),
            run_id="r",
            now=NOW,
            timezone=KST,
            ports=context().ports,
        )
        with pytest.raises(KeyError):
            await build_gate(ctx, LEARNING)


def _at(ctx: GrowthContext, now: datetime) -> GrowthContext:
    from dataclasses import replace

    return replace(ctx, now=now)


class TestConsent:
    async def test_동의는_기본값이_없다(self):
        consent = InMemoryConsent({CHILD: False})
        assert await consent.child_health_granted(child_id=CHILD) is False
        with pytest.raises(KeyError):
            await consent.child_health_granted(child_id=UUID(int=2))

    async def test_동의가_없으면_교육도_건강정보를_읽지_않는다(self):
        safety = InMemorySafety([SafetyEntry("allergy", "라텍스", "active")])
        ctx = context(birth_for(30), consent=False, safety=safety)
        gate = await build_gate(ctx, LEARNING)
        assert safety.calls == 0
        assert gate.consent_child_health is False
        assert gate.safety_ok is True  # 실패가 아니라 읽을 것이 없는 상태다
        assert gate.allergy_states == ()
        assert ctx.state.safety_entries == ()

    async def test_동의가_없으면_성장_기록을_읽지_않는다(self):
        log = InMemoryGrowthLog([measurement(1, date(2026, 3, 1), "95.0")])
        ctx = context(birth_for(48), consent=False, growth_log=log)
        gate = await build_gate(ctx, REVIEW)
        assert log.calls == 0
        assert gate.growth_log_count == 0
        assert ctx.state.measurements == ()


class TestSafetyRead:
    async def test_교육은_동의가_있으면_알레르기와_환경_알레르기를_읽는다(self):
        entries = [
            SafetyEntry("allergy", "라텍스", "active"),
            SafetyEntry("allergy", "땅콩", "retracted"),
            SafetyEntry("environmental", "꽃가루", "active"),
        ]
        safety = InMemorySafety(entries)
        ctx = context(birth_for(30), safety=safety)
        gate = await build_gate(ctx, LEARNING)
        assert safety.calls == 1
        assert gate.allergy_states == ("active", "retracted")  # allergy 종류만
        assert ctx.state.safety_entries == tuple(entries)  # 거르는 것은 필터다 — 전부 담는다
        assert ctx.state.rules is not None

    @pytest.mark.parametrize("label", [ROUTINE, BOOK, REVIEW])
    async def test_교육이_아니면_알레르기를_읽지_않는다(self, label):
        safety = InMemorySafety([SafetyEntry("allergy", "라텍스", "active")])
        ctx = context(birth_for(30), safety=safety, books=InMemoryBooks())
        gate = await build_gate(ctx, label)
        assert safety.calls == 0
        assert gate.allergy_states == ()

    async def test_조회_실패는_0행과_다르다(self):
        ctx = context(birth_for(30), safety=InMemorySafety(fail=True))
        gate = await build_gate(ctx, LEARNING)
        assert gate.safety_ok is False
        assert ctx.state.safety_entries == ()

    async def test_0_11개월_교육은_안전_정보를_읽지_않는다(self):
        """어차피 closed.infant_learning 으로 닫힌다."""
        safety = InMemorySafety([SafetyEntry("allergy", "라텍스", "active")])
        gate = await build_gate(context(birth_for(8), safety=safety), LEARNING)
        assert safety.calls == 0
        assert gate.safety_ok is True

    async def test_안전_사전을_못_읽으면_교육이_닫힌다(self):
        """빈 사전으로 통과시키지 않는다."""

        def broken():
            raise SafetyRulesUnavailable("사전 없음")

        from dataclasses import replace

        ctx = replace(context(birth_for(30)), safety_rules=broken)
        gate = await build_gate(ctx, LEARNING)
        assert gate.safety_ok is False
        assert ctx.state.rules is None

    async def test_루틴은_사전을_못_읽으면_예외를_그대로_올린다(self):
        def broken():
            raise SafetyRulesUnavailable("사전 없음")

        from dataclasses import replace

        ctx = replace(context(birth_for(30)), safety_rules=broken)
        with pytest.raises(SafetyRulesUnavailable):
            await build_gate(ctx, ROUTINE)


class TestGrowthLogRead:
    async def test_성장_추이는_측정을_한_번_전부_읽는다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.0", "14.0"),
            measurement(2, date(2026, 9, 1), "104.2", "17.1"),
        ]
        log = InMemoryGrowthLog(rows)
        ctx = context(birth_for(48), growth_log=log)
        gate = await build_gate(ctx, REVIEW)
        assert log.calls == 1
        assert gate.growth_log_count == 2
        assert ctx.state.measurements == tuple(rows)

    @pytest.mark.parametrize("label", [LEARNING, ROUTINE, BOOK])
    async def test_추천_라벨은_성장_기록을_읽지_않는다(self, label):
        log = InMemoryGrowthLog([measurement(1, date(2026, 3, 1), "95.0")])
        ctx = context(birth_for(30), growth_log=log, books=InMemoryBooks())
        gate = await build_gate(ctx, label)
        assert log.calls == 0
        assert gate.growth_log_count == 0


class TestDataReady:
    async def test_공지는_교육에서만_본다(self):
        notice = InMemoryNotice(has_notice=True)
        learning = await build_gate(context(birth_for(30), notice=notice), LEARNING)
        routine = await build_gate(context(birth_for(30), notice=notice), ROUTINE)
        assert (learning.data.notice, routine.data.notice) == (True, False)

    async def test_공지_표가_없으면_공지가_없다(self):
        gate = await build_gate(context(birth_for(30)), LEARNING)
        assert gate.data.notice is False

    async def test_도서_포트가_없으면_도서_API_가_닫힌다(self):
        gate = await build_gate(context(birth_for(30)), BOOK)
        assert gate.data.book_api is False  # 인증키가 없다

    async def test_도서_포트가_있으면_호출부가_넘긴_상태를_따른다(self):
        ctx = context(birth_for(30), books=InMemoryBooks())
        assert (await build_gate(ctx, BOOK)).data.book_api is True
        assert (await build_gate(ctx, BOOK, book_api_ok=False)).data.book_api is False


class TestForTask:
    async def test_task_마다_run_state_가_새로_만들어진다(self):
        ctx = context(birth_for(48), growth_log=InMemoryGrowthLog())
        await build_gate(ctx, REVIEW)
        other = ctx.for_task()
        assert ctx.state.birth_date is not None
        assert other.state.birth_date is None
        assert other.state is not ctx.state
        assert other.ports is ctx.ports


class TestRun:
    async def test_성장_추이는_모델_없이_끝까지_돈다(self):
        log = InMemoryGrowthLog(
            [
                measurement(1, date(2026, 3, 12), "95.0", "14.0"),
                measurement(2, date(2026, 9, 12), "105.0", "17.5"),
            ]
        )
        result = await run(
            task("growth_review", "6개월 동안 얼마나 컸어?"), context(growth_log=log)
        )
        assert result.status == "completed"
        assert result.model_calls == 0
        assert result.tools == ()
        assert result.agent == "growth"
        (readout,) = result.readouts
        assert readout.kind == "growth_delta" and readout.authored_by == "code"
        assert "2026년 3월 12일" in readout.body and "2026년 9월 12일" in readout.body

    async def test_동의가_없으면_성장_추이는_닫힘_문구만_나간다(self):
        log = InMemoryGrowthLog([measurement(1, date(2026, 3, 12), "95.0")])
        result = await run(task("growth_review"), context(consent=False, growth_log=log))
        assert result.status == "blocked" and result.model_calls == 0
        assert result.readouts == (READOUTS.render("closed.consent"),)
        assert log.calls == 0

    async def test_12개월_미만_교육은_닫힘_문구로_끝난다(self):
        result = await run(task("learning_suggestion"), context(birth_for(8)))
        assert result.status == "blocked" and result.model_calls == 0
        assert result.readouts == (READOUTS.render("closed.infant_learning"),)
        assert result.tools == ()

    async def test_알레르기_조회가_실패하면_교육만_닫힌다(self):
        safety = InMemorySafety(fail=True)
        learning = await run(task("learning_suggestion"), context(birth_for(30), safety=safety))
        routine = await run(task("routine_coaching", "양치 혼자 하게"), context(birth_for(30)))
        assert learning.readouts == (READOUTS.render("blocked.safety"),)
        assert routine.status == "mock"

    async def test_도서_API_가_없으면_도서만_닫힌다(self):
        result = await run(task("book_suggestion", "공룡 책"), context(birth_for(30)))
        assert result.readouts == (READOUTS.render("closed.book_api"),)

    async def test_의료_처치_요청은_모델을_부르지_않고_닫힌다(self):
        result = await run(
            task("routine_coaching", "약 먹는 걸 너무 싫어해. 어떻게 먹이지?"), context()
        )
        assert result.status == "blocked" and result.model_calls == 0
        assert result.readouts == (READOUTS.render("closed.medical_routine"),)

    async def test_증상_같은_행동은_교정안_없이_닫힌다(self):
        result = await run(task("routine_coaching", "눈을 자꾸 깜빡여. 어떻게 고쳐?"), context())
        assert result.readouts == (READOUTS.render("closed.symptom_habit"),)

    async def test_일반_루틴_요청은_닫히지_않고_열릴_tool_을_돌려준다(self):
        result = await run(
            task("routine_coaching", "양치를 혼자 하게 하려면 어떻게 해?"), context()
        )
        assert result.status == "mock"
        assert result.tools == (
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "propose_routine_plan",
        )

    async def test_run_state_에_gate_를_남긴다(self):
        ctx = context(birth_for(30))
        await run(task("routine_coaching", "양치 혼자 하게"), ctx)
        assert ctx.state.gate is not None and ctx.state.gate.stage.months == 30

    async def test_Growth_task_가_아니면_거부한다(self):
        other = DomainTask(
            run_id="r", agent="food", task_type="meal_recommendation", request_texts=()
        )
        with pytest.raises(ValueError):
            await run(other, context())
