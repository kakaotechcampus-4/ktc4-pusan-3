"""`medication` 라벨 실행기 — 조회 · 체크 안내는 코드로(모델 0회), 나머지는 모델 한 번.

- 이어받은 맥락(pending)이 있으면 **모델 없이** 이어 간다. 세 가지 — 중단 확인 · 대상 약 · 복용 시점
- "네" 는 표현 표로 가른다. 못 가린 답은 아무것도 쓰지 않고 다시 묻지도 않는다
- 중단 시각은 '네' 라고 답한 run 의 now 다(`updated_at`). 묻는 run 에서는 아무것도 바꾸지 않는다
- 쓰기는 중단 하나뿐이다. 묻는 run · 초안 · "먹였어" 뒤의 쓰기 카운터는 0 이다
- 되묻기 맥락의 `question` 과 결과의 `needs_observation` 은 같은 한 문장이다
"""

import json
from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest

from app.agents.health.agent import run
from app.agents.health.context import HealthRunState
from app.agents.health.labels.medication import (
    NO_WORDS,
    QUERY_WORDS,
    STOP_STEMS,
    YES_WORDS,
    confirm_answer,
)
from app.agents.health.prompt import build_system_prompt
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import (
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot
from tests.unit.agents.health.support import (
    KST,
    NOW,
    TODAY,
    FakeLLM,
    call,
    calls,
    context,
    task,
)

PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
CHECKED = datetime(2026, 9, 28, 8, 40, tzinfo=KST)


def antibiotic(**kwargs):
    return course_with_doses(
        "항생제", PATTERN, TODAY - timedelta(days=1), TODAY + timedelta(days=1), **kwargs
    )


def syrup():
    return course_with_doses("시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=2))


def world(*seeds, **kwargs):
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    return context(medication=store, **kwargs), store


def next_turn(ctx, result, *, minutes: int = 2):
    """앞 run 이 되묻고 멈춘 자리를 다음 run 이 이어받는다(연결 이슈의 reply_to 를 흉내 낸다).

    now 는 뒤로 옮긴다 — 중단 시각이 답한 run 의 now 인지 가리려고.
    """
    return replace(
        ctx,
        now=ctx.now + timedelta(minutes=minutes),
        state=HealthRunState(),
        pending=result.pending,
    )


def med(text: str):
    return task("medication", text)


class TestQueryAndCheck:
    @pytest.mark.parametrize("text", ["다음 약 언제 먹여야 해?", "항생제 언제 먹여야 해?"])
    async def test_다음_약_언제는_모델_없이(self, text):
        ctx, _ = world(antibiotic(checked={(TODAY, time(8, 30))}))
        llm = FakeLLM()

        result = await run(med(text), ctx, client=llm)

        assert llm.seen == [] and result.model_calls == 0
        assert result.status == "completed"
        [readout] = result.readouts
        assert readout.kind == "dose_status" and readout.title == "항생제"
        assert "오늘 체크 1/3회" in readout.body

    async def test_또_먹여도_돼는_사실만(self):
        # T11 — 허가도 거절도 하지 않는다. 마지막 체크 · 다음 예정 · 오늘 체크 수만 낸다
        ctx, _ = world(antibiotic(checked={(TODAY, time(8, 30))}))
        llm = FakeLLM()

        result = await run(med("지금 또 먹여도 돼?"), ctx, client=llm)

        assert llm.seen == [] and result.model_calls == 0
        [readout] = result.readouts
        assert readout.kind == "dose_status"
        for phrase in ("먹여도 돼", "안 돼", "먹이면 안", "괜찮", "가능"):
            assert phrase not in readout.body

    async def test_조회인데_복약이_없으면_없음_안내(self):
        ctx, _ = world()

        result = await run(med("다음 약 언제야?"), ctx, client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("notice.no_active_med").body]

    @pytest.mark.parametrize("text", ["아까 약 먹였어.", "점심 약 먹였어.", "저녁 약은 못 먹였어."])
    async def test_먹였어는_모델을_부르지_않는다(self, text):
        # T07 · T08 · T09 — 쓰지 않고 복약 화면 체크를 안내한다
        course, rows = antibiotic()
        ctx, store = world((course, rows))
        llm = FakeLLM()

        result = await run(med(text), ctx, client=llm)

        assert llm.seen == [] and result.model_calls == 0
        assert [r.body for r in result.readouts] == [READOUTS.render("dose.check_on_screen").body]
        assert store.writes == [] and result.medication_drafts == ()
        assert result.needs_observation == () and result.pending is None
        checked = [
            r
            for r in await store.doses(
                schedule_ids=[course.id],
                date_from=TODAY - timedelta(days=1),
                date_to=TODAY + timedelta(days=1),
            )
            if r.checked_at
        ]
        assert checked == []

    def test_조회_표현_표(self):
        assert QUERY_WORDS == ("다음 약", "언제 먹", "또 먹여도", "먹여도 돼", "더 먹여도")


class TestModelPath:
    async def test_새_약은_모델_한_번에_초안(self):
        ctx, store = world()
        text = "항생제 하루 세 번 식후에 5ml씩 먹여야 해."
        llm = FakeLLM(
            call(
                "create_medication_schedule",
                {"title_span": "항생제", "dosage_span": "5ml", "timing_spans": ["하루 세 번 식후"]},
            )
        )

        result = await run(med(text), ctx, client=llm)

        assert result.status == "completed" and result.model_calls == 1
        [draft] = result.medication_drafts
        assert (draft.op, draft.source, draft.schedule_id) == ("create", "utterance", None)
        assert draft.schedule["title"] == "항생제" and draft.schedule["dosage"] == "5ml"
        assert draft.notice_times == ("08:30", "12:30", "17:30")
        assert draft.missing == ("ends_on",)
        assert len(draft.doses) == 3
        assert [r.kind for r in result.readouts] == ["medication_notice"]
        assert store.writes == []

    async def test_모델에게는_쓸_tool_이름만_열린다(self):
        ctx, _ = world()
        llm = FakeLLM(call("stop_medication_schedule", {}))
        seen_tools: list[str] = []
        original = llm.chat

        async def spy(messages, **kwargs):
            seen_tools.extend(t["function"]["name"] for t in kwargs["tools"])
            return await original(messages, **kwargs)

        llm.chat = spy  # type: ignore[method-assign]

        await run(med("약 끊었어"), ctx, client=llm)

        assert sorted(seen_tools) == [
            "create_medication_schedule",
            "search_medication_schedules",
            "stop_medication_schedule",
            "update_medication_schedule",
        ]

    async def test_수정_초안은_채널로_나간다(self):
        course, rows = antibiotic(dosage="5ml")
        ctx, _ = world((course, rows))
        llm = FakeLLM(
            call(
                "update_medication_schedule",
                {"title_span": "항생제", "timing_spans": ["저녁 시간 7시"]},
            )
        )

        result = await run(med("항생제 저녁 시간 7시로 바꿔줘"), ctx, client=llm)

        [draft] = result.medication_drafts
        assert (draft.op, str(draft.schedule_id)) == ("update", str(course.id))
        assert draft.notice_times == ("08:30", "12:30", "19:00")
        assert draft.before is not None
        assert [d["scheduled_time"] for d in draft.before["doses"]] == ["08:30", "12:30", "17:30"]

    async def test_되묻고_멈추면_질문과_맥락이_같이_나간다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows))
        llm = FakeLLM(call("stop_medication_schedule", {"title_span": "항생제"}))

        result = await run(med("항생제 끊었어."), ctx, client=llm)

        question = READOUTS.render("med.stop_confirm", title="항생제").body
        assert result.needs_observation == (question,)
        assert result.pending is not None
        assert (result.pending.agent, result.pending.kind) == ("health", "medication_stop")
        assert result.pending.question == question
        assert store.writes == [] and result.medication_drafts == ()
        assert result.model_calls == 1

    async def test_출력_없이_끝나면_failed(self):
        ctx, _ = world()
        llm = FakeLLM()  # tool 없이 말로 끝낸다

        result = await run(med("항생제 하루 세 번 식후"), ctx, client=llm)

        assert result.status == "failed" and result.model_calls == 1
        assert result.medication_drafts == () and result.readouts == ()

    async def test_틀린_span_이면_고쳐서_다시_낸다(self):
        ctx, _ = world()
        llm = FakeLLM(
            call(
                "create_medication_schedule",
                {"title_span": "아목시실린", "timing_spans": ["하루 세 번 식후"]},
            ),
            call(
                "create_medication_schedule",
                {"title_span": "항생제", "timing_spans": ["하루 세 번 식후"]},
            ),
        )

        result = await run(med("항생제 하루 세 번 식후"), ctx, client=llm)

        assert result.status == "completed" and len(result.medication_drafts) == 1
        assert len(llm.seen) == 2 and result.model_calls == 1

    async def test_동의가_없으면_모델을_부르지_않는다(self):
        ctx, store = world(antibiotic(), consent=False)
        llm = FakeLLM(call("stop_medication_schedule", {}))

        result = await run(med("약 끊었어"), ctx, client=llm)

        assert result.status == "blocked" and llm.seen == []
        assert store.writes == []

    async def test_복약은_건강정보를_읽지_않는다(self):
        safety = InMemoryHealthSafety()
        ctx, _ = world(safety=safety)

        await run(med("아까 약 먹였어"), ctx, client=FakeLLM())

        assert safety.calls == 0

    async def test_프롬프트에_아이_이름과_건강정보가_없다(self):
        ctx, _ = world()

        prompt = build_system_prompt(ctx, HealthTaskType.MEDICATION)

        assert TODAY.isoformat() in prompt
        for forbidden in ("별이", "알레르기", "땅콩"):
            assert forbidden not in prompt

    async def test_프롬프트는_허가_판단을_금지한다(self):
        ctx, _ = world()

        prompt = build_system_prompt(ctx, HealthTaskType.MEDICATION)

        assert "원문" in prompt and "짐작" in prompt


class TestConfirmAnswer:
    @pytest.mark.parametrize(
        "text",
        [
            "응",
            "네",
            "예",
            "네네",
            "응응",
            "그래",
            "그래요",
            "맞아요",
            "멈춰줘",
            "끊어",
            "네 멈춰주세요",
        ],
    )
    def test_네(self, text):
        assert confirm_answer([text]) == "yes"

    @pytest.mark.parametrize(
        "text",
        ["아니요", "아니", "아냐", "아니 그냥 둬", "취소", "계속 먹일게", "그대로 둬", "말고"],
    )
    def test_아니요(self, text):
        assert confirm_answer([text]) == "no"

    @pytest.mark.parametrize(
        "text",
        ["글쎄", "잘 모르겠어", "그래서 어떻게 해?", "반응이 없어요", "응급실 가야 하나", ""],
    )
    def test_애매하면_unclear(self, text):
        assert confirm_answer([text]) == "unclear"

    def test_아니요가_하나라도_있으면_아니요(self):
        assert confirm_answer(["응 근데 아니 계속 먹일래"]) == "no"

    def test_표_상수(self):
        assert YES_WORDS == ("응", "네", "예", "그래", "멈춰", "끊어", "맞아", "좋아")
        assert NO_WORDS == ("아니", "아냐", "취소", "계속", "그대로", "말고")


class TestResumeStop:
    async def _asked(self, *seeds):
        ctx, store = world(*seeds)
        llm = FakeLLM(call("stop_medication_schedule", {"title_span": "항생제"}))
        first = await run(med("항생제 끊었어."), ctx, client=llm)
        assert first.pending is not None
        return ctx, store, first

    async def test_중단_질문에_네_하면_멈추고_중단_시각을_남긴다(self):
        course, rows = antibiotic(checked={(TODAY, time(8, 30)): CHECKED})
        ctx, store, first = await self._asked((course, rows))
        assert store.writes == []  # 묻는 run 은 아무것도 바꾸지 않았다
        second_ctx = next_turn(ctx, first)
        llm = FakeLLM()

        result = await run(med("응"), second_ctx, client=llm)

        assert llm.seen == [] and result.model_calls == 0
        assert result.status == "completed"
        assert store.stopped == {course.id: second_ctx.now}
        assert second_ctx.now == NOW + timedelta(minutes=2)  # 묻던 run 의 now 가 아니다
        assert [r.body for r in result.readouts] == [
            READOUTS.render("med.stopped", title="항생제").body
        ]
        assert result.needs_observation == () and result.pending is None
        # 회차 행과 체크는 그대로다
        kept = await store.doses(
            schedule_ids=[course.id],
            date_from=TODAY - timedelta(days=1),
            date_to=TODAY + timedelta(days=1),
        )
        assert len(kept) == 9 and [r.checked_at for r in kept if r.checked_at] == [CHECKED]

    @pytest.mark.parametrize("answer", ["아니 그냥 둬", "글쎄"])
    async def test_아니요도_애매해도_그대로(self, answer):
        ctx, store, first = await self._asked(antibiotic())

        result = await run(med(answer), next_turn(ctx, first), client=FakeLLM())

        assert store.writes == [] and store.stopped == {}
        assert [r.body for r in result.readouts] == [
            READOUTS.render("med.stop_kept", title="항생제").body
        ]
        assert result.pending is None

    async def test_이미_멈춘_약이면_시각을_바꾸지_않는다(self):
        course, rows = antibiotic()
        ctx, store, first = await self._asked((course, rows))
        # 묻고 답하는 사이에 다른 경로로 이미 멈췄다
        await store.stop_course(schedule_id=course.id, stopped_at=NOW - timedelta(hours=1))

        await run(med("응"), next_turn(ctx, first), client=FakeLLM())

        assert store.stopped == {course.id: NOW - timedelta(hours=1)}
        assert len(store.writes) == 1

    async def test_사라진_코스는_없다고_안내한다(self):
        ctx, store, first = await self._asked(antibiotic())
        empty = InMemoryMedicationStore()
        gone = replace(
            next_turn(ctx, first),
            ports=replace(ctx.ports, medication=empty, medication_writer=empty),
        )

        result = await run(med("응"), gone, client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("notice.no_active_med").body]
        assert empty.writes == []


class TestResumeWhich:
    async def _asked(self, op_text, tool, args):
        ctx, store = world(antibiotic(), syrup())
        first = await run(med(op_text), ctx, client=FakeLLM(call(tool, args)))
        assert first.pending is not None and first.pending.kind == "medication_which"
        return ctx, store, first

    async def test_약을_고르면_중단_확인으로_넘어간다(self):
        # T14 · C48-2med — 같은 질문을 다시 묻지 않는다. 중단 확인은 다른 질문이다
        ctx, store, first = await self._asked("그 약 끊어줘", "stop_medication_schedule", {})
        llm = FakeLLM()

        result = await run(med("항생제"), next_turn(ctx, first), client=llm)

        assert llm.seen == [] and result.model_calls == 0
        question = READOUTS.render("med.stop_confirm", title="항생제").body
        assert result.needs_observation == (question,)
        assert result.pending is not None and result.pending.kind == "medication_stop"
        assert result.pending.question != first.pending.question
        assert store.writes == []

    async def test_약을_고르면_수정_초안으로_이어_간다(self):
        ctx, store, first = await self._asked(
            "저녁 7시로 바꿔줘", "update_medication_schedule", {"timing_spans": ["저녁 7시"]}
        )
        llm = FakeLLM()

        result = await run(med("항생제"), next_turn(ctx, first), client=llm)

        assert llm.seen == [] and result.model_calls == 0
        [draft] = result.medication_drafts
        assert draft.op == "update" and draft.schedule["title"] == "항생제"
        assert draft.notice_times == ("08:30", "12:30", "19:00")
        assert store.writes == []

    @pytest.mark.parametrize("answer", ["글쎄", "약", "둘 다"])
    async def test_못_고른_답은_아무것도_쓰지_않는다(self, answer):
        ctx, store, first = await self._asked("그 약 끊어줘", "stop_medication_schedule", {})

        result = await run(med(answer), next_turn(ctx, first), client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("med.which_unclear").body]
        assert result.pending is None and result.needs_observation == ()  # 다시 묻지도 않는다
        assert result.medication_drafts == () and store.writes == []

    async def test_이름이_둘_다_들어_있으면_못_고른_것이다(self):
        ctx, store, first = await self._asked("그 약 끊어줘", "stop_medication_schedule", {})

        result = await run(med("항생제랑 시럽"), next_turn(ctx, first), client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("med.which_unclear").body]
        assert store.writes == []

    async def test_그_사이_멈춘_약은_후보에서_빠진다(self):
        ctx, store, first = await self._asked("그 약 끊어줘", "stop_medication_schedule", {})
        [syrup_course] = [
            c
            for c in await store.active_courses(child_id=ctx.child_id, today=TODAY)
            if c.title == "시럽"
        ]
        await store.stop_course(schedule_id=syrup_course.id, stopped_at=NOW)

        result = await run(med("시럽"), next_turn(ctx, first), client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("med.which_unclear").body]
        assert result.pending is None
        assert len(store.writes) == 1  # 위에서 직접 멈춘 한 건뿐


class TestResumeTiming:
    async def _asked(self, text="하루 세 번 먹여야 해."):
        ctx, store = world()
        llm = FakeLLM(call("create_medication_schedule", {"timing_spans": ["하루 세 번"]}))
        first = await run(med(text), ctx, client=llm)
        assert first.pending is not None and first.pending.kind == "dose_timing"
        assert first.medication_drafts == ()
        return ctx, store, first

    async def test_시점_답으로_초안을_만든다(self):
        # T02 + "식후요"
        ctx, store, first = await self._asked()
        llm = FakeLLM()

        result = await run(med("식후요"), next_turn(ctx, first), client=llm)

        assert llm.seen == [] and result.model_calls == 0
        [draft] = result.medication_drafts
        assert draft.notice_times == ("08:30", "12:30", "17:30")
        assert draft.missing == ("title", "ends_on")
        assert result.pending is None and store.writes == []

    async def test_시점을_못_풀면_초안을_내지_않는다(self):
        ctx, store, first = await self._asked()

        result = await run(med("몰라"), next_turn(ctx, first), client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("med.timing_unclear").body]
        assert result.medication_drafts == ()
        assert result.pending is None and result.needs_observation == ()
        assert store.writes == []

    async def test_수정_중이던_시점_답으로_수정_초안을_만든다(self):
        course, rows = antibiotic(dosage="5ml")
        ctx, store = world((course, rows))
        llm = FakeLLM(
            call(
                "update_medication_schedule",
                {"title_span": "항생제", "timing_spans": ["하루 두 번"]},
            )
        )
        first = await run(med("항생제 하루 두 번으로 바꿔줘"), ctx, client=llm)
        assert first.pending is not None and first.pending.kind == "dose_timing"

        result = await run(med("식후요"), next_turn(ctx, first), client=FakeLLM())

        [draft] = result.medication_drafts
        assert (draft.op, str(draft.schedule_id)) == ("update", str(course.id))
        assert draft.notice_times == ("08:30", "17:30")
        assert draft.schedule["dosage"] == "5ml"

    async def test_지금과_같은_시점으로_답하면_바뀐_게_없다고_안내한다(self):
        course, rows = antibiotic()  # 08:30 12:30 17:30
        ctx, store = world((course, rows))
        llm = FakeLLM(
            call(
                "update_medication_schedule",
                {"title_span": "항생제", "timing_spans": ["하루 세 번"]},
            )
        )
        first = await run(med("항생제 하루 세 번으로 바꿔줘"), ctx, client=llm)
        assert first.pending is not None and first.pending.kind == "dose_timing"

        result = await run(med("식후요"), next_turn(ctx, first), client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("med.no_change").body]
        assert result.medication_drafts == () and result.pending is None
        assert store.writes == []

    async def test_맥락에_발화_원문이_없다(self):
        text = "열이 많이 나서 병원 갔다 왔는데 항생제 하루 세 번 먹이래"
        ctx, _ = world()
        llm = FakeLLM(
            call(
                "create_medication_schedule",
                {"title_span": "항생제", "timing_spans": ["하루 세 번"]},
            )
        )

        first = await run(med(text), ctx, client=llm)

        dumped = json.dumps(dict(first.pending.payload), ensure_ascii=False)
        assert text not in dumped and "열이 많이" not in dumped


class TestResumeFailures:
    @pytest.mark.parametrize("kind", ["unknown_kind", ""])
    async def test_모르는_맥락은_아무것도_쓰지_않고_failed(self, kind):
        from app.agents.common.result import DomainPending

        ctx, store = world(antibiotic())
        pending = DomainPending(agent="health", kind=kind, question="?", payload={})

        result = await run(med("응"), replace(ctx, pending=pending), client=FakeLLM())

        assert result.status == "failed" and result.model_calls == 0
        assert store.writes == []

    async def test_다른_Agent_의_맥락은_받지_않는다(self):
        from app.agents.common.result import DomainPending

        ctx, store = world(antibiotic())
        pending = DomainPending(
            agent="memory",
            kind="medication_stop",
            question="?",
            payload={"schedule_id": "x", "title": "t"},
        )

        result = await run(med("응"), replace(ctx, pending=pending), client=FakeLLM())

        assert result.status == "failed" and store.writes == []

    async def test_망가진_맥락은_failed(self):
        from app.agents.common.result import DomainPending

        ctx, store = world(antibiotic())
        pending = DomainPending(agent="health", kind="medication_stop", question="?", payload={})

        result = await run(med("응"), replace(ctx, pending=pending), client=FakeLLM())

        assert result.status == "failed" and store.writes == []


# ── 최종 리뷰 반영 ──
class TestActionCuesBeatTables:
    """조회 · 복용 표는 요청을 삼키지 않는다 — 등록 · 수정 · 중단 말이 같이 있으면 모델이 본다."""

    async def test_다_나아서_끊는다는_말은_체크_안내가_아니다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows))
        llm = FakeLLM(call("stop_medication_schedule", {"title_span": None}))

        result = await run(med("어제까지 먹였는데 다 나아서 이제 끊을게"), ctx, client=llm)

        assert len(llm.seen) == 1 and result.model_calls == 1
        assert result.needs_observation == (
            READOUTS.render("med.stop_confirm", title="항생제").body,
        )
        assert store.writes == []

    async def test_먹였다는_말이_섞여도_수정_요청은_수정이다(self):
        ctx, _ = world(antibiotic())
        llm = FakeLLM(
            call(
                "update_medication_schedule",
                {"title_span": None, "timing_spans": ["저녁 시간 7시"]},
            )
        )

        result = await run(med("3일 먹였는데 저녁 시간 7시로 바꿔줘"), ctx, client=llm)

        assert result.model_calls == 1
        [draft] = result.medication_drafts
        assert draft.op == "update" and draft.notice_times == ("08:30", "12:30", "19:00")

    async def test_못_먹였다는_말이_섞여도_등록_요청은_등록이다(self):
        ctx, _ = world()
        llm = FakeLLM(
            call(
                "create_medication_schedule", {"title_span": "해열제", "timing_spans": ["오후 3시"]}
            )
        )

        result = await run(
            med("약 안 먹였는데 오후 3시에 새로 받은 해열제 등록해줘"), ctx, client=llm
        )

        assert result.model_calls == 1
        [draft] = result.medication_drafts
        assert draft.schedule["title"] == "해열제" and draft.notice_times == ("15:00",)

    async def test_언제_먹여야_하는지_말하며_새_약을_알려도_등록이다(self):
        ctx, _ = world()
        llm = FakeLLM(call("create_medication_schedule", {"timing_spans": ["하루 세 번 식후"]}))

        result = await run(med("새 약 받았어 언제 먹여야 해? 하루 세 번 식후래"), ctx, client=llm)

        assert result.model_calls == 1
        [draft] = result.medication_drafts
        assert draft.notice_times == ("08:30", "12:30", "17:30")

    @pytest.mark.parametrize("text", ["다음 약 언제 먹여야 해?", "아까 약 먹였어."])
    async def test_수정_표현이_없으면_표가_그대로_받는다(self, text):
        ctx, _ = world(antibiotic())
        llm = FakeLLM()

        result = await run(med(text), ctx, client=llm)

        assert llm.seen == [] and result.model_calls == 0


class TestMultipleToolCalls:
    async def test_한_응답에_중단이_둘이면_첫째만_묻고_나머지는_알린다(self):
        ctx, store = world(antibiotic(), syrup())
        llm = FakeLLM(
            calls(
                ("stop_medication_schedule", {"title_span": "항생제"}),
                ("stop_medication_schedule", {"title_span": "시럽"}),
            )
        )

        result = await run(med("항생제랑 시럽 둘 다 끊었어"), ctx, client=llm)

        assert result.needs_observation == (
            READOUTS.render("med.stop_confirm", title="항생제").body,
        )
        assert result.pending is not None and result.pending.payload["title"] == "항생제"
        assert [r.body for r in result.readouts] == [READOUTS.render("med.one_at_a_time").body]
        assert store.writes == []


class TestAmbiguousClockResume:
    async def _asked(self):
        ctx, store = world()
        llm = FakeLLM(
            call("create_medication_schedule", {"title_span": "항생제", "timing_spans": ["8시에"]})
        )
        first = await run(med("항생제 8시에 먹여야 해"), ctx, client=llm)
        assert first.needs_observation == (READOUTS.render("ask.dose_clock").body,)
        return ctx, store, first

    @pytest.mark.parametrize("answer", ["저녁이요", "저녁 8시요"])
    async def test_오전_오후로_답하면_초안을_만든다(self, answer):
        ctx, _, first = await self._asked()
        llm = FakeLLM()

        result = await run(med(answer), next_turn(ctx, first), client=llm)

        assert llm.seen == [] and result.model_calls == 0
        [draft] = result.medication_drafts
        assert draft.notice_times == ("20:00",) and draft.schedule["title"] == "항생제"

    async def test_다른_답이면_알아듣지_못했다고_하고_끝낸다(self):
        ctx, store, first = await self._asked()

        result = await run(med("식후요"), next_turn(ctx, first), client=FakeLLM())

        assert [r.body for r in result.readouts] == [READOUTS.render("med.timing_unclear").body]
        assert result.pending is None and result.medication_drafts == ()
        assert store.writes == []


class TestUpdateTwiceInOneResponse:
    async def test_한_응답의_수정_둘은_초안_한_장으로_나간다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows))
        llm = FakeLLM(
            calls(
                ("update_medication_schedule", {"title_span": "항생제", "dosage_span": "7ml"}),
                (
                    "update_medication_schedule",
                    {"title_span": "항생제", "timing_spans": ["저녁 시간 7시"]},
                ),
            )
        )

        result = await run(med("항생제 7ml 로 늘리고 저녁 시간 7시로 바꿔줘"), ctx, client=llm)

        [draft] = result.medication_drafts
        assert draft.schedule["dosage"] == "7ml"
        assert draft.notice_times == ("08:30", "12:30", "19:00")
        assert [d["scheduled_time"] for d in draft.before["doses"]] == ["08:30", "12:30", "17:30"]
        assert len(result.readouts) == 1 and store.writes == []


class TestNothingToChange:
    async def test_바꿀_값이_없는_말은_모델_한_번으로_끝난다(self):
        # 용량 · 허가를 묻는 말에 모델이 update 를 title 만 넣어 불러도 6번을 돌지 않고 끝난다
        ctx, store = world(antibiotic())
        llm = FakeLLM(call("update_medication_schedule", {"title_span": "항생제"}))

        result = await run(med("항생제 양 늘려도 돼?"), ctx, client=llm)

        assert result.status == "completed" and result.model_calls == 1
        assert len(llm.seen) == 1
        assert [r.body for r in result.readouts] == [READOUTS.render("med.no_change").body]
        assert result.medication_drafts == () and store.writes == []
        assert result.needs_observation == () and result.pending is None

    async def test_되돌린_수정은_빈_결과가_아니라_안내가_나간다(self):
        ctx, _ = world(antibiotic(dosage="5ml"))  # 원래 5ml — 7ml 로 고쳤다가 5ml 로 돌아온다
        llm = FakeLLM(
            calls(
                ("update_medication_schedule", {"title_span": "항생제", "dosage_span": "7ml"}),
                ("update_medication_schedule", {"title_span": "항생제", "dosage_span": "5ml"}),
            )
        )

        result = await run(med("항생제 7ml 로 했다가 5ml 로 돌려줘"), ctx, client=llm)

        assert result.status == "completed" and result.medication_drafts == ()
        assert [r.body for r in result.readouts] == [READOUTS.render("med.no_change").body]

    async def test_프롬프트가_바꿀_게_없는_말의_길을_알려_준다(self):
        ctx, _ = world()

        prompt = build_system_prompt(ctx, HealthTaskType.MEDICATION)

        assert "title_span 만" in prompt and "용량" in prompt


class TestStopCueWords:
    async def test_끊이지_않는다는_말은_중단_요청이_아니다(self):
        # "끊" 한 글자가 "끊이지" 에도 걸려 체크 안내가 모델로 새면 묻지 않은 중단 질문이 나간다
        ctx, _ = world(antibiotic())
        llm = FakeLLM()

        result = await run(med("기침이 끊이지 않아서 아까 해열제 먹였어"), ctx, client=llm)

        assert llm.seen == [] and result.model_calls == 0
        assert [r.body for r in result.readouts] == [READOUTS.render("dose.check_on_screen").body]

    @pytest.mark.parametrize(
        "text", ["먹였는데 끊었어", "먹였는데 이제 끊을게", "먹였는데 끊어줘", "먹였는데 끊고 싶어"]
    )
    async def test_끊는다는_말은_모델이_본다(self, text):
        ctx, _ = world(antibiotic())
        llm = FakeLLM(call("stop_medication_schedule", {"title_span": None}))

        result = await run(med(text), ctx, client=llm)

        assert result.model_calls == 1
        assert result.needs_observation == (
            READOUTS.render("med.stop_confirm", title="항생제").body,
        )


class TestCardThenQuestionSameCourse:
    async def test_한_응답의_수정과_시점_되묻기는_카드만_남긴다(self):
        ctx, store = world(antibiotic())
        llm = FakeLLM(
            calls(
                ("update_medication_schedule", {"title_span": "항생제", "dosage_span": "7ml"}),
                (
                    "update_medication_schedule",
                    {"title_span": "항생제", "timing_spans": ["하루 두 번"]},
                ),
            )
        )

        result = await run(med("항생제 7ml 로 늘리고 하루 두 번으로 바꿔줘"), ctx, client=llm)

        [draft] = result.medication_drafts
        assert draft.schedule["dosage"] == "7ml"
        assert result.pending is None and result.needs_observation == ()
        assert READOUTS.render("med.one_at_a_time") in result.readouts
        assert store.writes == []


class TestResumeKeepsSymptomCodes:
    async def test_시점을_되물은_뒤에도_이유의_증상_코드가_실린다(self):
        ctx, _ = world()
        llm = FakeLLM(
            call(
                "create_medication_schedule",
                {
                    "title_span": "항생제",
                    "timing_spans": ["하루 세 번"],
                    "indication_span": "열이 나서",
                },
            )
        )
        first = await run(med("항생제 열이 나서 하루 세 번 먹여야 해."), ctx, client=llm)
        assert first.pending is not None

        result = await run(med("식후요"), next_turn(ctx, first), client=FakeLLM())

        [draft] = result.medication_drafts
        assert draft.schedule["symptom_codes"] == ["fever"]


class TestConfirmAnswerNumeral:
    """ "네 번 남았어요" 의 네는 숫자다. 중단을 쓰는 유일한 경로라 '네' 로 읽으면 알림이 멈춘다."""

    @pytest.mark.parametrize(
        "text",
        [
            "네 번 남았는데 다 먹일게요",
            "아직 네 번 남았어요",
            "네 번째 약이에요",
            "하루에 네번 먹여요",
            "네 시간 뒤에 또 먹여요",
            "네 개 남았어",
        ],
    )
    def test_숫자_네는_네가_아니다(self, text):
        assert confirm_answer([text]) == "unclear"

    @pytest.mark.parametrize(
        "text", ["네, 네 번 남았어요", "네 번 남았지만 끊어줘", "네 네 번 남았어요"]
    )
    def test_진짜_네와_같이_말하면_네다(self, text):
        assert confirm_answer([text]) == "yes"


class TestConfirmAnswerStopWords:
    """멈추겠다는 분명한 말은 '네' 다. 어미가 붙어야 센다 — "그만" 만으로는 질문에도 걸린다."""

    @pytest.mark.parametrize(
        "text",
        [
            "중단해 주세요",
            "중단해줘",
            "중단할게요",
            "그만할게요",
            "그만둘게요",
            "그만해줘",
            "끊을게요",
            "끊을래요",
            "멈출게요",
        ],
    )
    def test_멈추겠다는_말은_네다(self, text):
        assert confirm_answer([text]) == "yes"

    @pytest.mark.parametrize(
        "text",
        [
            "중단하지 마세요",
            "그만 먹여도 돼?",
            "그만할까요",
            "중단 안 할래요",
            "끊지 마",
            "끊이지 않아요",
            "멈추지 마세요",
            "그만",
            "중단",
        ],
    )
    def test_멈추지_말라는_말과_질문은_모른다로_둔다(self, text):
        assert confirm_answer([text]) == "unclear"

    def test_아니요가_있으면_멈춘다는_말이_있어도_아니요다(self):
        assert confirm_answer(["아니 그만할게요 말고 계속"]) == "no"

    def test_어간_상수(self):
        assert STOP_STEMS == ("중단", "그만둘", "그만", "끊을", "멈출")


class TestConfirmAnswerNegation:
    """ "안 끊을게요" 는 멈추지 않겠다는 말이다. 어간 + 어미로만 읽으면 '네' 가 되어 반대로 쓴다."""

    @pytest.mark.parametrize(
        "text",
        [
            "안 끊을게요",
            "안 멈출게요",
            "안 그만둘게요",
            "안 중단할게요",
            "못 끊을게요",
            "안끊을게요",
            "응 안 끊을게요",
        ],
    )
    def test_부정이_붙은_멈춤_말은_네가_아니다(self, text):
        assert confirm_answer([text]) == "unclear"

    def test_아니요가_함께면_아니요다(self):
        assert confirm_answer(["아니 안 끊을게요"]) == "no"


class TestConflictThenAnswer:
    async def test_식전_식후가_엇갈린_말은_답으로_풀린다(self):
        ctx, _ = world()
        llm = FakeLLM(
            call(
                "create_medication_schedule",
                {"title_span": "항생제", "timing_spans": ["아침은 식전 저녁은 식후"]},
            )
        )
        first = await run(med("항생제 아침은 식전 저녁은 식후로 먹여야 해"), ctx, client=llm)
        assert first.pending is not None and first.pending.kind == "dose_timing"
        assert first.needs_observation == (READOUTS.render("ask.dose_timing").body,)

        result = await run(med("식후요"), next_turn(ctx, first), client=FakeLLM())

        [draft] = result.medication_drafts
        assert draft.notice_times == ("08:30", "17:30")


class TestUnreadablePeriodLabel:
    async def test_못_읽는_기간으로_고쳐_달라는_말에_안내가_나간다(self):
        ctx, store = world(antibiotic(dosage="5ml"))
        llm = FakeLLM(
            call(
                "update_medication_schedule",
                {"title_span": "항생제", "period_span": "다음 주 월요일까지"},
            )
        )

        result = await run(med("항생제 다음 주 월요일까지로 늘려줘"), ctx, client=llm)

        assert result.status == "completed" and result.model_calls == 1
        assert [r.body for r in result.readouts] == [READOUTS.render("med.period_unclear").body]
        assert result.medication_drafts == () and store.writes == []


class TestNoActiveCourseUpdate:
    async def test_진행_중인_복약이_없으면_모델_한_번으로_끝난다(self):
        # 용량 질문 → update(title 만). 코스가 없어도 실패 루프가 되지 않는다
        ctx, store = world()
        llm = FakeLLM(call("update_medication_schedule", {"title_span": "항생제"}))

        result = await run(med("항생제 양 늘려도 돼?"), ctx, client=llm)

        assert result.status == "completed" and result.model_calls == 1
        assert [r.body for r in result.readouts] == [READOUTS.render("notice.no_active_med").body]
        assert result.medication_drafts == () and result.pending is None
        assert store.writes == []
