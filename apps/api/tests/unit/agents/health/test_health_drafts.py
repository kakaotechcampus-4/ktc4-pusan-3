"""복약 초안 tool — create · update 는 초안만 내고, stop 은 한 번 묻고 바꾸지 않는다.

- 초안은 DB 를 건드리지 않는다 (in-memory 저장소의 쓰기 카운터가 0)
- 약 이름 · 용량 · 이유 · 시점 · 기간은 요청 원문의 부분 문자열이다. 아니면 거절한다
- 시점이 빠지면("하루 세 번") 짐작하지 않고 되묻는다 — 초안 0 · 이어갈 맥락(pending)만 남긴다
- 기간이 없으면 ends_on 을 짐작하지 않고 missing 으로 넘긴다
- 중단은 묻는 run 에서 아무것도 바꾸지 않는다. 약이 둘이고 이름이 없으면 어느 약인지 먼저 묻는다
- 맥락(pending)에는 검증된 값만 싣고 발화 원문 전체는 싣지 않는다
"""

import json
from datetime import date, time, timedelta

import pytest

from app.agents.health.readouts import READOUTS
from app.agents.health.result import ErrorCode
from app.agents.health.schemas.medication import (
    CreateMedicationArgs,
    StopMedicationArgs,
    UpdateMedicationArgs,
)
from app.agents.health.store.inmemory import InMemoryMedicationStore, course_with_doses
from app.agents.health.store.ports import DoseSlot
from app.agents.health.tools.drafts import (
    Ambiguous,
    create_medication_schedule,
    finish_create,
    pick_course,
    stop_medication_schedule,
    update_for,
    update_medication_schedule,
)
from app.agents.health.tools.timing import ResolvedDose
from tests.unit.agents.health.support import TODAY, birth_for, context

PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
T01 = "항생제 하루 세 번 식후에 5ml씩 먹여야 해."
T05 = "열 나서 병원 갔더니 중이염이래. 항생제 하루 세 번 식후로 5일 먹이래."


def antibiotic(**kwargs):
    return course_with_doses(
        "항생제",
        PATTERN,
        TODAY - timedelta(days=1),
        TODAY + timedelta(days=1),
        dosage="5ml",
        storage="fridge",
        note="흔들어서",
        **kwargs,
    )


def syrup():
    return course_with_doses("시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=2))


def world(*seeds, text: str = "", birth: date | None = None):
    """(코스, 회차 행) 들을 넣은 저장소와, 요청 원문을 알고 있는 context."""
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    ctx = context(birth, medication=store)
    ctx.state.request_texts = (text,)
    return ctx, store


def create_args(**kwargs) -> CreateMedicationArgs:
    base = dict(
        title_span=None, dosage_span=None, timing_spans=[], period_span=None, indication_span=None
    )
    return CreateMedicationArgs(**{**base, **kwargs})


def update_args(**kwargs) -> UpdateMedicationArgs:
    base = dict(title_span=None, dosage_span=None, timing_spans=None, period_span=None)
    return UpdateMedicationArgs(**{**base, **kwargs})


class TestCreate:
    async def test_항생제_하루_세_번_식후_5ml(self):
        ctx, _ = world(text=T01)

        result = await create_medication_schedule(
            ctx,
            create_args(title_span="항생제", dosage_span="5ml", timing_spans=["하루 세 번 식후"]),
        )

        assert result.success
        [draft] = ctx.state.drafts
        assert (draft.op, draft.source) == ("create", "utterance")
        assert draft.schedule.title == "항생제" and draft.schedule.dosage == "5ml"
        assert draft.schedule.indication_text is None
        assert (draft.schedule.storage, draft.schedule.med_form, draft.schedule.note) == (None,) * 3
        assert len(draft.doses) == 3
        assert draft.notice_times == ["08:30", "12:30", "19:30"]
        assert draft.schedule.starts_on == TODAY
        assert draft.schedule.ends_on is None and draft.missing == ["ends_on"]

    async def test_초안은_아무것도_쓰지_않는다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows), text=T01)

        await create_medication_schedule(
            ctx, create_args(title_span="항생제", timing_spans=["하루 세 번 식후"])
        )
        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 7시"])
        )

        assert store.writes == [] and store.stopped == {}
        [still] = await store.active_courses(child_id=ctx.child_id, today=TODAY)
        assert (still.status, still.pattern) == ("active", PATTERN)

    async def test_확인_문구가_readout_으로_쌓인다(self):
        ctx, _ = world(text=T01)

        await create_medication_schedule(
            ctx, create_args(title_span="항생제", timing_spans=["하루 세 번 식후"])
        )

        [readout] = ctx.state.readouts
        assert readout.authored_by == "code" and readout.kind == "medication_notice"
        assert "08:30 · 12:30 · 19:30" in readout.body and "항생제" in readout.body

    async def test_중이염_이유는_원문_그대로(self):
        ctx, _ = world(text=T05)

        await create_medication_schedule(
            ctx,
            create_args(
                title_span="항생제",
                timing_spans=["하루 세 번 식후"],
                period_span="5일",
                indication_span="중이염",
            ),
        )

        [draft] = ctx.state.drafts
        assert draft.schedule.indication_text == "중이염"
        assert draft.schedule.ends_on == TODAY + timedelta(days=4)
        assert draft.missing == []

    async def test_읽을_수_없는_기간은_ends_on_을_비운다(self):
        ctx, _ = world(text="항생제 하루 세 번 식후, 다음 주 월요일까지")

        await create_medication_schedule(
            ctx,
            create_args(
                title_span="항생제",
                timing_spans=["하루 세 번 식후"],
                period_span="다음 주 월요일까지",
            ),
        )

        [draft] = ctx.state.drafts
        assert draft.schedule.ends_on is None and draft.missing == ["ends_on"]

    async def test_약_이름이_비면_title_이_missing(self):
        ctx, _ = world(text="오후 3시에 먹여야 해")

        await create_medication_schedule(ctx, create_args(timing_spans=["오후 3시"]))

        [draft] = ctx.state.drafts
        assert draft.missing == ["title", "ends_on"]
        assert draft.notice_times == ["15:00"]

    async def test_영아는_자기_전이_19시_30분(self):
        ctx, _ = world(text="시럽 자기 전에 먹여야 해", birth=birth_for(12))

        await create_medication_schedule(
            ctx, create_args(title_span="시럽", timing_spans=["자기 전"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["19:30"]
        assert (draft.doses[0].timing_anchor, draft.doses[0].fixed_time) == ("fixed", time(19, 30))

    async def test_시점이_없으면_시점_맥락을_남긴다(self):
        ctx, store = world(text="하루 세 번 먹여야 해.")

        result = await create_medication_schedule(ctx, create_args(timing_spans=["하루 세 번"]))

        assert result.success  # 루프를 끝낸다 — 되묻고 멈춘다
        assert ctx.state.drafts == []
        assert ctx.state.question == READOUTS.render("ask.dose_timing").body
        pending = ctx.state.pending
        assert pending is not None
        assert (pending.agent, pending.kind) == ("health", "dose_timing")
        assert pending.question == ctx.state.question
        assert pending.payload["op"] == "create"
        assert pending.payload["timing_spans"] == ["하루 세 번"]
        assert pending.payload["draft"]["title"] is None
        assert store.writes == []

    async def test_맥락에는_검증된_값만_싣는다(self):
        text = "항생제 5ml 하루 세 번 먹여야 해. 아침에 일어나자마자 병원 갔다 왔고 열이 많이 났어."
        ctx, _ = world(text=text)

        await create_medication_schedule(
            ctx,
            create_args(title_span="항생제", dosage_span="5ml", timing_spans=["하루 세 번"]),
        )

        payload = ctx.state.pending.payload
        assert payload["draft"]["title"] == "항생제" and payload["draft"]["dosage"] == "5ml"
        dumped = json.dumps(payload, ensure_ascii=False)  # JSON 으로 바뀌는 값만이다
        assert text not in dumped and "열이 많이" not in dumped

    @pytest.mark.parametrize(
        "args",
        [
            dict(title_span="항생제", dosage_span="10ml", timing_spans=["하루 세 번 식후"]),
            dict(title_span="아목시실린", timing_spans=["하루 세 번 식후"]),
            dict(title_span="항생제", timing_spans=["하루 네 번 식후"]),
            dict(title_span="항생제", timing_spans=["하루 세 번 식후"], indication_span="폐렴"),
            dict(title_span="항생제", timing_spans=["하루 세 번 식후"], period_span="7일"),
        ],
    )
    async def test_원문에_없는_값은_거절(self, args):
        ctx, _ = world(text=T01)

        result = await create_medication_schedule(ctx, create_args(**args))

        assert result.success is False
        assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS
        assert ctx.state.drafts == [] and ctx.state.pending is None

    async def test_거절_메시지에_원문을_싣지_않는다(self):
        ctx, _ = world(text=T01)

        result = await create_medication_schedule(
            ctx, create_args(title_span="아목시실린", timing_spans=["하루 세 번 식후"])
        )

        assert "아목시실린" not in json.dumps(result.error, ensure_ascii=False)


class TestUpdate:
    async def test_저녁을_7시로_바꾸면_update_초안(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 저녁 시간 7시로 바꿔줘")

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 시간 7시"])
        )

        assert result.success
        [draft] = ctx.state.drafts
        assert (draft.op, draft.schedule_id) == ("update", str(course.id))
        assert [d.scheduled_time for d in draft.before.doses] == [
            time(8, 30),
            time(12, 30),
            time(19, 30),
        ]
        # 저녁 회차만 바뀐다 — 아침 · 점심은 그대로다
        assert draft.notice_times == ["08:30", "12:30", "19:00"]
        dinner = next(d for d in draft.doses if d.scheduled_time == time(19, 0))
        assert (dinner.timing_anchor, dinner.fixed_time) == ("fixed", time(19, 0))
        assert "19:30" not in draft.notice_times

    async def test_말하지_않은_칸은_지금_값_그대로(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 7ml 로 바꿔줘")

        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))

        [draft] = ctx.state.drafts
        assert draft.schedule.dosage == "7ml"
        assert (draft.schedule.title, draft.schedule.storage, draft.schedule.note) == (
            "항생제",
            "fridge",
            "흔들어서",
        )
        assert draft.schedule.starts_on == course.starts_on
        assert draft.schedule.ends_on == course.ends_on
        assert [d.scheduled_time for d in draft.doses] == [
            d.scheduled_time for d in draft.before.doses
        ]
        assert draft.before.schedule.dosage == "5ml"

    async def test_기간만_바꾸면_ends_on_만_바뀐다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 5일 더 먹이래")

        await update_medication_schedule(ctx, update_args(title_span="항생제", period_span="5일"))

        [draft] = ctx.state.drafts
        assert draft.schedule.ends_on == TODAY + timedelta(days=4)
        assert draft.schedule.starts_on == course.starts_on

    async def test_시점을_못_풀면_시점_맥락에_대상을_싣는다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows), text="항생제 하루 두 번으로 바꿔줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["하루 두 번"])
        )

        assert ctx.state.drafts == []
        pending = ctx.state.pending
        assert pending is not None and pending.kind == "dose_timing"
        assert pending.payload["op"] == "update"
        assert pending.payload["schedule_id"] == str(course.id)
        assert pending.payload["timing_spans"] == ["하루 두 번"]
        assert store.writes == []

    async def test_바꿀_값이_없으면_초안을_내지_않는다(self):
        # "약 양 좀 늘려도 돼?" 처럼 바꿀 값을 말하지 않았는데 같은 값으로 카드가 뜨면 안 된다
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 양 좀 늘려도 돼?")

        result = await update_medication_schedule(ctx, update_args(title_span="항생제"))

        assert result.success and result.data["draft"] is False
        assert ctx.state.drafts == []

    async def test_같은_값으로_바꾸면_초안을_내지_않는다(self):
        course, rows = antibiotic()  # 용량 5ml · 08:30 12:30 19:30
        ctx, _ = world((course, rows), text="항생제 5ml 하루 세 번 식후")

        result = await update_medication_schedule(
            ctx,
            update_args(title_span="항생제", dosage_span="5ml", timing_spans=["하루 세 번 식후"]),
        )

        assert result.success and result.data["draft"] is False
        assert ctx.state.drafts == []

    async def test_진행_중인_복약이_없으면_안내로_끝난다(self):
        # 실패로 돌려주면 `tool_choice="required"` 라 모델이 같은 호출을 되풀이한다 — stop 과 같다
        ctx, _ = world(text="항생제 저녁 7시로 바꿔줘")

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 7시"])
        )

        assert result.success and result.data["draft"] is False
        assert ctx.state.drafts == []
        assert [r.body for r in ctx.state.readouts] == [
            READOUTS.render("notice.no_active_med").body
        ]

    async def test_멈춘_코스는_고치지_않는다(self):
        # 중단 시각(updated_at)을 지킨다 — 멈춘 코스는 진행 중 목록에 없다
        stopped = antibiotic(status="stopped")
        ctx, _ = world(stopped, text="항생제 저녁 7시로 바꿔줘")

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 7시"])
        )

        # 멈춘 코스는 진행 중이 아니라 고치지 않는다 — 안내로 끝난다
        assert result.success and result.data["draft"] is False
        assert ctx.state.drafts == []
        assert [r.body for r in ctx.state.readouts] == [
            READOUTS.render("notice.no_active_med").body
        ]

    async def test_약이_둘이고_이름이_없으면_되묻고_이어갈_값을_남긴다(self):
        ctx, _ = world(antibiotic(), syrup(), text="저녁 7시로 바꿔줘")

        await update_medication_schedule(ctx, update_args(timing_spans=["저녁 7시"]))

        assert ctx.state.drafts == []
        pending = ctx.state.pending
        assert pending is not None and pending.kind == "medication_which"
        assert pending.payload["op"] == "update"
        assert pending.payload["args"]["timing_spans"] == ["저녁 7시"]
        assert [c["title"] for c in pending.payload["candidates"]] == ["항생제", "시럽"]


class TestStop:
    async def test_약_끊었어는_한_번_묻고_바꾸지_않는다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows), text="항생제 끊었어.")

        result = await stop_medication_schedule(ctx, StopMedicationArgs(title_span="항생제"))

        assert result.success
        assert ctx.state.question == READOUTS.render("med.stop_confirm", title="항생제").body
        pending = ctx.state.pending
        assert pending is not None
        assert (pending.agent, pending.kind) == ("health", "medication_stop")
        assert pending.question == ctx.state.question
        assert pending.payload == {"schedule_id": str(course.id), "title": "항생제"}
        assert ctx.state.drafts == []
        assert store.writes == [] and store.stopped == {}
        assert [c.id for c in await store.active_courses(child_id=ctx.child_id, today=TODAY)] == [
            course.id
        ]

    async def test_약이_하나면_이름이_없어도_그_약(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="그 약 끊어줘")

        await stop_medication_schedule(ctx, StopMedicationArgs(title_span=None))

        assert ctx.state.pending.kind == "medication_stop"
        assert ctx.state.pending.payload["schedule_id"] == str(course.id)

    async def test_약이_둘이고_이름이_없으면_되묻는다(self):
        ctx, store = world(antibiotic(), syrup(), text="그 약 끊어줘")

        result = await stop_medication_schedule(ctx, StopMedicationArgs(title_span=None))

        assert result.success
        assert ctx.state.question == READOUTS.render("ask.which_med", titles="항생제 · 시럽").body
        pending = ctx.state.pending
        assert pending is not None and pending.kind == "medication_which"
        assert pending.payload["op"] == "stop"
        assert len(pending.payload["candidates"]) == 2
        assert ctx.state.drafts == [] and store.writes == []

    async def test_이름이_하나만_맞으면_그_약을_묻는다(self):
        target, rows = syrup()
        ctx, _ = world(antibiotic(), (target, rows), text="시럽 끊어줘")

        await stop_medication_schedule(ctx, StopMedicationArgs(title_span="시럽"))

        assert ctx.state.pending.kind == "medication_stop"
        assert ctx.state.pending.payload["schedule_id"] == str(target.id)

    async def test_원문에_없는_이름은_거절(self):
        ctx, store = world(antibiotic(), text="그 약 끊어줘")

        result = await stop_medication_schedule(ctx, StopMedicationArgs(title_span="항생제"))

        assert result.success is False
        assert ctx.state.pending is None and store.writes == []

    async def test_진행_중인_복약이_없으면_없다고_안내한다(self):
        ctx, store = world(text="약 끊었어")

        result = await stop_medication_schedule(ctx, StopMedicationArgs(title_span=None))

        assert result.success
        [readout] = ctx.state.readouts
        assert readout.body == READOUTS.render("notice.no_active_med").body
        assert ctx.state.pending is None and ctx.state.question is None and store.writes == []


class TestPickCourse:
    def courses(self, *seeds):
        return [course for course, _ in seeds]

    def test_없으면_None(self):
        assert pick_course([], "항생제") is None

    def test_이름이_없고_하나면_그_약(self):
        [course] = self.courses(antibiotic())

        assert pick_course([course], None) == course

    def test_이름이_없고_둘이면_모호하다(self):
        picked = pick_course(self.courses(antibiotic(), syrup()), None)

        assert isinstance(picked, Ambiguous) and len(picked.candidates) == 2

    def test_이름이_하나만_맞으면_그_약(self):
        courses = self.courses(antibiotic(), syrup())

        assert pick_course(courses, "시럽") == courses[1]

    def test_코스_이름이_더_길어도_맞는다(self):
        course, _ = course_with_doses("항생제 시럽", PATTERN, TODAY, TODAY)

        assert pick_course([course], "항생제") == course

    def test_이름이_안_맞으면_하나뿐이어도_모호하다(self):
        # 엉뚱한 약의 알림이 끊기지 않게 — 임의로 고르지 않는다
        [course] = self.courses(antibiotic())

        picked = pick_course([course], "타이레놀")

        assert isinstance(picked, Ambiguous) and picked.candidates == (course,)

    def test_이름이_둘에_다_맞으면_모호하다(self):
        a, _ = course_with_doses("항생제 가", PATTERN, TODAY, TODAY)
        b, _ = course_with_doses("항생제 나", PATTERN, TODAY, TODAY)

        assert isinstance(pick_course([a, b], "항생제"), Ambiguous)


class TestPendingContext:
    async def test_HealthContext_는_이어받은_맥락을_들고_있다(self):
        ctx, _ = world(text="")

        assert ctx.pending is None
        assert ctx.for_task().state is not ctx.state


class TestUpdateWhichDose:
    async def test_이미_고친_저녁_회차도_다시_바뀐다(self):
        # 저녁 회차가 fixed 19:00 으로 바뀐 코스 — anchor 가 dinner 가 아니어도 저녁 회차를 찾는다
        pattern = (
            DoseSlot.of("breakfast", 30),
            DoseSlot.of("lunch", 30),
            DoseSlot.of("fixed", fixed_time=time(19, 0)),
        )
        seed = course_with_doses("항생제", pattern, TODAY, TODAY + timedelta(days=2))
        ctx, _ = world(seed, text="항생제 저녁 8시로 바꿔줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 8시"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "20:00"]

    async def test_끼니를_말하지_않으면_패턴_전체를_바꾼다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 하루 두 번 식후로 바꿔줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["하루 두 번 식후"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "19:30"]

    async def test_저녁_식전으로_바꾸면_저녁_회차만_바뀐다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 저녁은 식전으로 바꿔줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁은 식전"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "18:30"]


# ── 최종 리뷰 반영 ──
class TestMoveDose:
    async def test_저녁_약은_자기_전으로는_저녁_회차를_자기_전으로_옮긴다(self):
        # "저녁 약은 자기 전으로" — 점심이 지워지거나 19:00 저녁이 새로 생기면 안 된다
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 저녁 약은 자기 전으로 바꿔줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 약은 자기 전으로"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "20:30"]

    async def test_기준점이_다른_회차는_가깝다고_지우지_않는다(self):
        # 저녁 식후(19:30)는 자기 전(20:30)과 한 시간 차이지만 다른 회차다 — 그대로 두고 더한다
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 자기 전에도 먹여줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["자기 전"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "19:30", "20:30"]

    async def test_말한_끼니가_패턴에_없으면_더하기만_한다(self):
        pattern = (DoseSlot.of("breakfast", 30), DoseSlot.of("dinner", 30))
        seed = course_with_doses("항생제", pattern, TODAY, TODAY + timedelta(days=2))
        ctx, _ = world(seed, text="항생제 점심 12시 반으로")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["점심 12시 반"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "19:30"]


class TestOneQuestionAtATime:
    async def test_되묻기가_둘이면_첫째만_남기고_나머지는_거절한다(self):
        ctx, store = world(antibiotic(), syrup(), text="항생제랑 시럽 둘 다 끊었어")
        first = await stop_medication_schedule(ctx, StopMedicationArgs(title_span="항생제"))

        second = await stop_medication_schedule(ctx, StopMedicationArgs(title_span="시럽"))

        assert first.success
        assert second.success is False
        assert second.error is not None and second.error["code"] == ErrorCode.INVALID_ARGS
        assert ctx.state.question == READOUTS.render("med.stop_confirm", title="항생제").body
        assert ctx.state.pending.payload["title"] == "항생제"  # 덮어쓰지 않았다
        # 못 물은 약이 있다고 보호자에게 알린다
        assert [r.body for r in ctx.state.readouts] == [READOUTS.render("med.one_at_a_time").body]
        assert store.writes == []

    async def test_안내는_한_번만_붙는다(self):
        ctx, _ = world(antibiotic(), syrup(), text="항생제 시럽 둘 다 끊어줘")
        await stop_medication_schedule(ctx, StopMedicationArgs(title_span="항생제"))

        await stop_medication_schedule(ctx, StopMedicationArgs(title_span="시럽"))
        await stop_medication_schedule(ctx, StopMedicationArgs(title_span="시럽"))

        assert len(ctx.state.readouts) == 1

    async def test_시점을_묻는_생성_둘도_첫째만_남는다(self):
        ctx, _ = world(text="기침약 하루 세 번, 해열제 하루 두 번")
        await create_medication_schedule(
            ctx, create_args(title_span="기침약", timing_spans=["하루 세 번"])
        )

        result = await create_medication_schedule(
            ctx, create_args(title_span="해열제", timing_spans=["하루 두 번"])
        )

        assert result.success is False
        assert ctx.state.pending.payload["draft"]["title"] == "기침약"
        assert ctx.state.drafts == []

    async def test_초안_둘은_각각_나간다(self):
        # 되묻는 것이 아니라 카드 두 장이다 — 서로 덮어쓰지 않는다
        ctx, _ = world(text="기침약 하루 세 번 식후, 해열제 오후 3시")

        await create_medication_schedule(
            ctx, create_args(title_span="기침약", timing_spans=["하루 세 번 식후"])
        )
        await create_medication_schedule(
            ctx, create_args(title_span="해열제", timing_spans=["오후 3시"])
        )

        assert [d.schedule.title for d in ctx.state.drafts] == ["기침약", "해열제"]


class TestAmbiguousClockQuestion:
    async def test_애매한_시각은_오전_오후를_묻는다(self):
        ctx, _ = world(text="항생제 8시에 먹여야 해")

        result = await create_medication_schedule(
            ctx, create_args(title_span="항생제", timing_spans=["8시에"])
        )

        assert result.success
        assert ctx.state.question == READOUTS.render("ask.dose_clock").body
        assert ctx.state.pending is not None and ctx.state.pending.kind == "dose_timing"
        assert ctx.state.drafts == []

    async def test_끼니_없는_횟수만_말하면_식전_식후를_묻는다(self):
        ctx, _ = world(text="하루 세 번 먹여야 해.")

        await create_medication_schedule(ctx, create_args(timing_spans=["하루 세 번"]))

        assert ctx.state.question == READOUTS.render("ask.dose_timing").body


class TestUpdateStacksOnOneCard:
    """같은 약의 수정은 일정(DraftBook · changed)처럼 한 장에 쌓인다.

    각 카드는 전체 값을 들고 있어서, 두 장이 나가면 나중에 제출한 카드가 앞 카드의 변경을 되돌린다.
    """

    TEXT = "항생제 7ml 로 늘리고 저녁 시간 7시로 바꿔줘"

    async def _two(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows), text=self.TEXT)
        first = await update_medication_schedule(
            ctx, update_args(title_span="항생제", dosage_span="7ml")
        )
        second = await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 시간 7시"])
        )
        return ctx, course, first, second

    async def test_같은_약을_두_번_고치면_초안_한_장에_쌓인다(self):
        ctx, course, first, second = await self._two()

        [draft] = ctx.state.drafts
        assert draft.schedule.dosage == "7ml"  # 앞 호출의 변경이 남아 있다
        assert draft.notice_times == ["08:30", "12:30", "19:00"]  # 뒤 호출의 변경도 들어 있다
        assert first.data["draft_id"] == second.data["draft_id"] == draft.draft_id

    async def test_before_는_처음_원본_그대로다(self):
        ctx, course, _, _ = await self._two()

        [draft] = ctx.state.drafts
        assert draft.before.schedule.dosage == "5ml"
        assert [d.scheduled_time for d in draft.before.doses] == [
            time(8, 30),
            time(12, 30),
            time(19, 30),
        ]
        assert draft.schedule_id == str(course.id)

    async def test_확인_문구는_마지막_것_하나만_남는다(self):
        ctx, _, _, _ = await self._two()

        [readout] = ctx.state.readouts
        assert "19:00" in readout.body and "19:30" not in readout.body

    async def test_바뀐_필드_이름을_tool_결과로_돌려준다(self):
        _, _, first, second = await self._two()

        assert first.data["changed"] == ["dosage"]
        assert second.data["changed"] == ["dosage", "doses"]  # 앞 호출의 것이 이어진다

    async def test_payload_에는_changed_를_싣지_않는다(self):
        ctx, _, _, _ = await self._two()

        [draft] = ctx.state.drafts
        assert "changed" not in draft.to_payload()  # 화면은 before 와 비교한다 — 일정과 같다

    async def test_다른_약은_각각_따로_나간다(self):
        ctx, store = world(antibiotic(), syrup(), text="항생제 7ml 로, 시럽 저녁 7시로")
        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))
        await update_medication_schedule(
            ctx, update_args(title_span="시럽", timing_spans=["저녁 7시"])
        )

        titles = [d.schedule.title for d in ctx.state.drafts]
        assert sorted(titles) == ["시럽", "항생제"] and len(ctx.state.readouts) == 2

    async def test_앞_호출을_되돌려_처음_값이_되면_초안을_거둔다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 7ml 로 늘렸다가 5ml 로 돌려줘")
        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))

        back = await update_medication_schedule(
            ctx, update_args(title_span="항생제", dosage_span="5ml")
        )

        assert back.success and back.data["draft"] is False
        assert ctx.state.drafts == []
        # 앞 초안의 확인 문구(7ml)는 거두고 바뀐 게 없다는 안내만 남는다
        assert [r.body for r in ctx.state.readouts] == [READOUTS.render("med.no_change").body]

    async def test_새_약_초안은_합치지_않는다(self):
        ctx, _ = world(text="기침약 하루 세 번 식후, 해열제 하루 세 번 식후")

        await create_medication_schedule(
            ctx, create_args(title_span="기침약", timing_spans=["하루 세 번 식후"])
        )
        await create_medication_schedule(
            ctx, create_args(title_span="해열제", timing_spans=["하루 세 번 식후"])
        )

        assert len(ctx.state.drafts) == 2


class TestNoChangeEndsCleanly:
    """바꿀 것이 없으면 모델이 거기서 끝낼 수 있어야 한다 — 일정의 draft=False 와 같다.

    출력 tool 이 모두 쓰는 tool 이라 실패로 돌려주면 모델이 6번을 더 돈다(`tool_choice="required"`).
    """

    async def test_바꿀_값이_없으면_초안_없이_성공으로_끝낸다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 양 좀 늘려도 돼?")

        result = await update_medication_schedule(ctx, update_args(title_span="항생제"))

        assert result.success and result.data["draft"] is False and result.data["changed"] == []
        assert ctx.state.drafts == []
        assert [r.body for r in ctx.state.readouts] == [READOUTS.render("med.no_change").body]

    async def test_같은_안내는_한_번만_붙는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 양 좀 늘려도 돼?")

        await update_medication_schedule(ctx, update_args(title_span="항생제"))
        await update_medication_schedule(ctx, update_args(title_span="항생제"))

        assert len(ctx.state.readouts) == 1

    async def test_이어받는_길은_실패_코드로_알린다(self):
        # 되묻기를 이어받는 run 은 update_for 를 직접 부른다 — 실패 코드로 med.no_change 를 고른다
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="")

        result = await update_for(
            ctx, course, dosage_span=None, timing_spans=None, period_span=None
        )

        assert result.success is False
        assert result.error is not None and result.error["code"] == ErrorCode.NO_CHANGE


class TestSameCourseCardOrQuestion:
    """한 약에 카드와 되묻기를 한 run 에 같이 내지 않는다.

    질문 뒤 다음 run 이 DB 에서 새 카드를 만들면 같은 약 카드가 둘이 되어, 승인 순서가 최종 값을
    정한다(전체 값을 든 카드가 서로 되돌린다).
    """

    async def test_카드를_낸_약의_시점_되묻기는_하지_않는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 7ml 로 늘리고 하루 두 번으로 바꿔줘")
        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["하루 두 번"])
        )

        assert result.success is False
        assert ctx.state.pending is None and ctx.state.question is None
        [draft] = ctx.state.drafts
        assert draft.schedule.dosage == "7ml"
        assert READOUTS.render("med.one_at_a_time") in ctx.state.readouts

    async def test_되묻는_중인_약은_또_고치지_않는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 하루 두 번으로 바꾸고 7ml 로 늘려줘")
        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["하루 두 번"])
        )
        asked = ctx.state.pending

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", dosage_span="7ml")
        )

        assert result.success is False
        assert ctx.state.drafts == [] and ctx.state.pending is asked
        assert READOUTS.render("med.one_at_a_time") in ctx.state.readouts

    async def test_카드를_낸_약의_중단_확인은_하지_않는다(self):
        course, rows = antibiotic()
        ctx, store = world((course, rows), text="항생제 저녁 7시로 바꾸고 끊어줘")
        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 7시"])
        )

        result = await stop_medication_schedule(ctx, StopMedicationArgs(title_span="항생제"))

        assert result.success is False
        assert ctx.state.pending is None and len(ctx.state.drafts) == 1
        assert store.writes == []

    async def test_중단을_묻는_약은_또_고치지_않는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 끊고 7ml 로 바꿔줘")
        await stop_medication_schedule(ctx, StopMedicationArgs(title_span="항생제"))

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", dosage_span="7ml")
        )

        assert result.success is False
        assert ctx.state.drafts == [] and ctx.state.pending.kind == "medication_stop"

    async def test_다른_약의_질문은_막지_않는다(self):
        ctx, _ = world(antibiotic(), syrup(), text="항생제 7ml 로 바꾸고 시럽은 끊어줘")
        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))

        result = await stop_medication_schedule(ctx, StopMedicationArgs(title_span="시럽"))

        assert result.success
        assert len(ctx.state.drafts) == 1 and ctx.state.pending.kind == "medication_stop"


class TestSymptomCodes:
    """복약 이유의 증상 코드는 모델이 고르지 않는다 — Food 처럼 Agent 코드가 사전으로 맞춘다.

    제출 API 는 받은 값을 그대로 저장하고, 사전을 따로 읽지 않는다.
    """

    async def _create(self, text, span):
        ctx, _ = world(text=text)
        result = await create_medication_schedule(
            ctx,
            create_args(
                title_span="항생제", indication_span=span, timing_spans=["하루 세 번 식후"]
            ),
        )
        assert result.success
        return ctx.state.drafts[0]

    @pytest.mark.parametrize(
        ("span", "codes"),
        [
            ("열이 나서", ["fever"]),
            ("열이 나고 기침도 해서", ["fever", "cough"]),
            ("열이 없는데 기침해서", ["cough"]),  # 사전의 취소 표현("열이 없")은 발열이 아니다
            ("중이염 때문에", []),  # 사전에 없는 말은 코드로 바꾸지 않는다
            (None, []),
        ],
    )
    async def test_복약_이유를_사전으로_맞춘다(self, span, codes):
        text = f"항생제 {span or ''} 하루 세 번 식후"

        draft = await self._create(text, span)

        assert draft.schedule.symptom_codes == codes
        assert draft.to_payload()["schedule"]["symptom_codes"] == codes

    async def test_수정_초안은_저장된_코드를_그대로_싣는다(self):
        # 사전이 바뀐 뒤의 옛 값("cough")도 다시 계산하지 않는다 — 제출 API 는 받은 값을 저장한다
        course, rows = antibiotic(indication_text="열이 나서", symptom_codes=("cough",))
        ctx, _ = world((course, rows), text="항생제 7ml 로 늘려줘")

        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))

        [draft] = ctx.state.drafts
        assert draft.schedule.symptom_codes == ["cough"]
        assert draft.before.schedule.symptom_codes == ["cough"]
        assert ctx.state.changed[draft.draft_id] == ("dosage",)  # 코드가 바뀐 것으로 세지 않는다

    async def test_이유가_없어도_저장된_코드는_지우지_않는다(self):
        course, rows = antibiotic(symptom_codes=("fever",))
        ctx, _ = world((course, rows), text="항생제 7ml 로 늘려줘")

        await update_medication_schedule(ctx, update_args(title_span="항생제", dosage_span="7ml"))

        [draft] = ctx.state.drafts
        assert draft.schedule.symptom_codes == ["fever"]

    def test_모델_인자에는_증상_코드_칸이_없다(self):
        for model in (CreateMedicationArgs, UpdateMedicationArgs):
            assert "symptom_codes" not in model.model_fields


class TestUnreadablePeriod:
    """못 읽은 기간을 "바꿀 내용이 없어요" 로 뭉개지 않는다 — 보호자는 이미 그 날짜인 줄 안다."""

    async def test_못_읽는_기간만_말하면_못_읽었다고_알린다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 다음 주 월요일까지로 늘려줘")

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", period_span="다음 주 월요일까지")
        )

        assert result.success and result.data["draft"] is False
        assert [r.body for r in ctx.state.readouts] == [READOUTS.render("med.period_unclear").body]

    async def test_다른_변경이_있으면_카드와_함께_알린다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 7ml 로 다음 주 월요일까지")

        await update_medication_schedule(
            ctx,
            update_args(title_span="항생제", dosage_span="7ml", period_span="다음 주 월요일까지"),
        )

        [draft] = ctx.state.drafts
        assert draft.schedule.dosage == "7ml" and draft.schedule.ends_on == course.ends_on
        assert READOUTS.render("med.period_unclear") in ctx.state.readouts
        assert READOUTS.render("med.no_change") not in ctx.state.readouts

    async def test_기간을_읽으면_알리지_않는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 5일 더 먹여")

        await update_medication_schedule(ctx, update_args(title_span="항생제", period_span="5일"))

        assert READOUTS.render("med.period_unclear") not in ctx.state.readouts

    async def test_같은_안내는_한_번만_붙는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 다음 주 월요일까지")
        args = update_args(title_span="항생제", period_span="다음 주 월요일까지")

        await update_medication_schedule(ctx, args)
        await update_medication_schedule(ctx, args)

        assert len(ctx.state.readouts) == 1


class TestNearestIsFixedOnly:
    """anchor 로 못 찾았을 때 가까운 회차로 찾는 것은 `fixed`(시각으로 고친) 회차뿐이다.

    저녁(19:00)과 자기 전(20:30)은 90분 차이다. 기준점이 있는 회차까지 "가장 가깝다" 고
    옮기면 말하지 않은 회차가 조용히 사라진다.
    """

    async def test_저녁_7시로_바꿔도_자기_전_회차는_남는다(self):
        pattern = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("bedtime"))
        seed = course_with_doses("항생제", pattern, TODAY, TODAY + timedelta(days=2))
        ctx, _ = world(seed, text="항생제 저녁 7시도 먹여줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 7시"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "19:00", "20:30"]

    async def test_일어나서도_먹여줘는_아침_회차를_지우지_않는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 일어나서도 먹여줘")

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["일어나서"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["07:30", "08:30", "12:30", "19:30"]

    async def test_시각으로_고쳐_둔_저녁_회차는_계속_찾는다(self):
        # 저녁 7시로 한 번 고친 코스(저녁이 fixed)를 다시 고친다 — anchor 로는 못 찾아도 옮겨진다
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 저녁 7시로, 다시 저녁 8시로")
        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 7시"])
        )

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["저녁 8시"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "20:00"]


class TestSameTimeAsks:
    """같은 시각 회차가 둘이 되면 초안을 내지 않고 시각을 하나씩 묻는다.

    전에는 계약 검증(같은 시각 둘 금지)에 걸려 INVALID_ARGS 를 돌려줬다. span 은 원문 복사라 모델이
    고칠 수 없어 같은 호출을 되풀이하다 failed 로 끝났다.
    """

    async def test_새_약의_시각이_겹치면_시각을_하나씩_묻는다(self):
        ctx, store = world(text="항생제 저녁 먹고 30분 뒤랑 자기 전", birth=birth_for(12))

        result = await create_medication_schedule(
            ctx,
            create_args(title_span="항생제", timing_spans=["저녁 먹고 30분 뒤", "자기 전"]),
        )

        assert result.success and ctx.state.drafts == []
        pending = ctx.state.pending
        assert pending is not None and pending.kind == "dose_timing"
        assert pending.payload["same_time"] is True
        assert ctx.state.question == READOUTS.render("ask.dose_same_time").body
        assert store.writes == []

    async def test_수정으로_남은_회차와_겹치면_시각을_하나씩_묻는다(self):
        # 영아 코스(저녁 식후 19:30)에 "자기 전에도" — 영아 자기 전은 고정 19:30
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 자기 전에도 먹여줘", birth=birth_for(12))

        result = await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["자기 전"])
        )

        assert result.success and ctx.state.drafts == []
        pending = ctx.state.pending
        assert pending is not None and pending.payload["same_time"] is True
        assert pending.payload["schedule_id"] == str(course.id)
        assert ctx.state.question == READOUTS.render("ask.dose_same_time").body

    async def test_겹치지_않으면_묻지_않는다(self):
        course, rows = antibiotic()
        ctx, _ = world((course, rows), text="항생제 자기 전에도 먹여줘", birth=birth_for(48))

        await update_medication_schedule(
            ctx, update_args(title_span="항생제", timing_spans=["자기 전"])
        )

        [draft] = ctx.state.drafts
        assert draft.notice_times == ["08:30", "12:30", "19:30", "20:30"]
        assert ctx.state.pending is None


class TestContractRejectionEndsCleanly:
    def test_화면에_못_나갈_초안은_실패가_아니라_안내로_끝낸다(self):
        # 마지막 안전망 — 계약 검증에 걸리면 모델에게 실패를 돌려주지 않는다(모델이 고칠 수 없다)
        ctx, _ = world(text="항생제")
        twice = [ResolvedDose("dinner", 30, None), ResolvedDose("fixed", 0, time(19, 30))]

        result = finish_create(
            ctx,
            title="항생제",
            dosage=None,
            indication_text=None,
            starts_on=TODAY,
            ends_on=None,
            timing=twice,
        )

        assert result.success and result.data["draft"] is False
        assert ctx.state.drafts == []
        assert [r.body for r in ctx.state.readouts] == [READOUTS.render("med.timing_unclear").body]
