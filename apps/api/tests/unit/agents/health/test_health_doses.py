"""다음 예정 · 체크 안내 · 코스 조회 — 체크는 보호자가 화면에서 하고, Agent 는 읽기만 한다.

- "먹였어" · "못 먹였어" 에는 아무것도 쓰지 않고 복약 화면에서 체크해 달라고만 안내한다
- 체크 안 된 회차는 "안 먹였다" 가 아니라 "체크 안 됨" 이다. 어떤 출력에도 그렇게 쓰지 않는다
- 마지막 체크는 체크를 누른 시각이 아니라 **회차 예정 시각**이다 (몰아서 누르면 실제와 어긋난다)
- 오늘 체크 수는 오늘 날짜 회차 중 `checked_at` 이 있는 것만 센다
- `can_take` 같은 허가 필드가 없다
"""

import json
from dataclasses import fields, replace
from datetime import datetime, time, timedelta

import pytest

from app.agents.common.refs import Ref
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.medication import SearchMedicationArgs
from app.agents.health.store.inmemory import InMemoryMedicationStore, course_with_doses
from app.agents.health.store.ports import DoseSlot
from app.agents.health.tools.doses import (
    DOSE_WORDS,
    DoseStatus,
    check_on_screen,
    dose_status_readouts,
    get_next_dose,
    search_medication_schedules,
)
from tests.unit.agents.health.support import KST, TODAY, context

PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
MORNING_10 = datetime(2026, 9, 28, 10, 0, tzinfo=KST)  # 오늘 08:30 은 지났고 12:30 은 아직


def world(*seeds, now: datetime = MORNING_10):
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    return replace(context(medication=store), now=now), store


def antibiotic(checked=(), **kwargs):
    return course_with_doses(
        "항생제",
        PATTERN,
        TODAY - timedelta(days=1),
        TODAY + timedelta(days=1),
        dosage="5ml",
        checked=checked,
        **kwargs,
    )


class TestCheckOnScreen:
    @pytest.mark.parametrize(
        "text", ["아까 약 먹였어.", "점심 약 먹였어.", "새벽 3시에 해열제 먹였어."]
    )
    def test_먹였어_표현은_걸린다(self, text):
        assert any(word in text for word in DOSE_WORDS)

    @pytest.mark.parametrize(
        "text",
        [
            "저녁 약은 못 먹였어.",
            "어제 약 안 먹였어요",
        ],
    )
    def test_못_먹였어_표현도_걸린다(self, text):
        assert any(word in text for word in DOSE_WORDS)

    @pytest.mark.parametrize(
        "text",
        [
            "항생제 하루 세 번 식후에 5ml씩 먹여야 해.",
            "다음 약 언제 먹여야 해?",
            "지금 또 먹여도 돼?",
            "항생제 끊었어.",
        ],
    )
    def test_복약_등록_조회_중단_표현은_안_걸린다(self, text):
        assert not any(word in text for word in DOSE_WORDS)

    async def test_먹였어는_쓰지_않고_화면_안내(self):
        ctx, store = world(antibiotic())

        readout = await check_on_screen(ctx)

        assert readout.body == READOUTS.render("dose.check_on_screen").body
        assert readout.authored_by == "code"
        assert store.writes == [] and store.stopped == {}

    async def test_못_먹였어도_쓰지_않는다(self):
        # 체크를 대신 남기지도, "안 먹였다" 로 채우지도 않는다 — 같은 안내다
        ctx, store = world(antibiotic())

        readout = await check_on_screen(ctx)

        assert readout.body == "복용 체크는 복약 화면에서 해 주세요."
        assert store.writes == []
        rows = await store.doses(
            schedule_ids=[
                c.id for c in await store.active_courses(child_id=ctx.child_id, today=TODAY)
            ],
            date_from=TODAY - timedelta(days=1),
            date_to=TODAY + timedelta(days=1),
        )
        assert all(row.checked_at is None for row in rows)

    async def test_복약이_없으면_체크_안내_대신_없음(self):
        ctx, store = world()

        readout = await check_on_screen(ctx)

        assert readout.body == READOUTS.render("notice.no_active_med").body
        assert readout.source_refs == ()
        assert store.writes == []

    async def test_체크_안내에_진행_중_코스_ref_만_단다(self):
        # ref 는 화면이 가리킬 코스다 — 체크할 수 없는 코스를 싣으면 안 된다
        running = [antibiotic(), antibiotic()]
        ended = course_with_doses(
            "시럽", PATTERN, TODAY - timedelta(days=5), TODAY - timedelta(days=1)
        )
        stopped = course_with_doses(
            "연고", PATTERN, TODAY - timedelta(days=1), TODAY + timedelta(days=1), status="stopped"
        )
        ctx, _ = world(*running, ended, stopped)

        readout = await check_on_screen(ctx)

        assert readout.body == READOUTS.render("dose.check_on_screen").body
        assert {ref.id for ref in readout.source_refs} == {course.id for course, _ in running}
        assert all(ref.kind == "medication_schedule" for ref in readout.source_refs)

    async def test_끝난_복약만_있어도_없음(self):
        ended = course_with_doses(
            "항생제", PATTERN, TODAY - timedelta(days=5), TODAY - timedelta(days=1)
        )
        ctx, _ = world(ended)

        assert (await check_on_screen(ctx)).body == READOUTS.render("notice.no_active_med").body


class TestGetNextDose:
    async def test_다음_예정과_오늘_체크_수(self):
        # 오늘 3회차 중 08:30 만 체크 — 지금(10:00) 이후 첫 회차는 12:30
        ctx, _ = world(antibiotic(checked={(TODAY, time(8, 30))}))

        [status] = await get_next_dose(ctx)

        assert status.title == "항생제"
        assert (status.checked_today, status.planned_today) == (1, 3)
        assert status.next_scheduled == datetime(2026, 9, 28, 12, 30, tzinfo=KST)

    async def test_오늘_체크_수는_체크된_것만_센다(self):
        # 어제 회차를 체크했어도 오늘 수에는 안 든다 · 체크 안 된 회차는 세지 않는다
        yesterday = TODAY - timedelta(days=1)
        ctx, _ = world(antibiotic(checked={(yesterday, time(8, 30)), (yesterday, time(12, 30))}))

        [status] = await get_next_dose(ctx)

        assert (status.checked_today, status.planned_today) == (0, 3)

    async def test_마지막_체크는_회차_예정_시각(self):
        # 08:30 회차를 11:50 에 몰아서 눌렀다 — 마지막 체크는 08:30 회차다
        pressed = datetime(2026, 9, 28, 9, 50, tzinfo=KST)
        ctx, _ = world(antibiotic(checked={(TODAY, time(8, 30)): pressed}))

        [status] = await get_next_dose(ctx)

        assert status.last_checked == datetime(2026, 9, 28, 8, 30, tzinfo=KST)
        assert status.last_checked != pressed

    async def test_마지막_체크는_가장_늦은_회차(self):
        yesterday = TODAY - timedelta(days=1)
        ctx, _ = world(antibiotic(checked={(yesterday, time(19, 30)), (TODAY, time(8, 30))}))

        [status] = await get_next_dose(ctx)

        assert status.last_checked == datetime(2026, 9, 28, 8, 30, tzinfo=KST)

    async def test_체크가_없으면_마지막은_None(self):
        ctx, _ = world(antibiotic())

        [status] = await get_next_dose(ctx)

        assert status.last_checked is None

    async def test_지금_이후_회차가_없으면_다음은_None(self):
        ended_today = course_with_doses(
            "시럽", (DoseSlot.of("breakfast"),), TODAY - timedelta(days=1), TODAY
        )
        ctx, _ = world(ended_today, now=datetime(2026, 9, 28, 20, 0, tzinfo=KST))

        [status] = await get_next_dose(ctx)

        assert status.next_scheduled is None

    async def test_코스마다_따로_낸다(self):
        syrup = course_with_doses(
            "시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=1)
        )
        ctx, _ = world(antibiotic(), syrup)

        statuses = await get_next_dose(ctx)

        assert [s.title for s in statuses] == ["항생제", "시럽"]
        assert [(s.checked_today, s.planned_today) for s in statuses] == [(0, 3), (0, 1)]

    async def test_복약이_없으면_빈_튜플(self):
        ctx, _ = world()

        assert await get_next_dose(ctx) == ()

    async def test_dose_status_는_코스마다_ref_를_달아_서로_구별된다(self):
        # 같은 이름 · 같은 횟수의 코스 둘이 같은 값의 readout 으로 합쳐지지 않는다
        first, second = antibiotic(), antibiotic()
        ctx, _ = world(first, second)

        readouts = await dose_status_readouts(ctx)

        assert [r.source_refs for r in readouts] == [
            (Ref("medication_schedule", course.id),) for course, _ in (first, second)
        ]
        assert readouts[0] != readouts[1]

    async def test_DoseStatus_에_허가_필드가_없다(self):
        assert {f.name for f in fields(DoseStatus)} == {
            "schedule_id",
            "title",
            "last_checked",
            "next_scheduled",
            "checked_today",
            "planned_today",
        }


class TestDoseStatusReadouts:
    async def test_사실만_나간다(self):
        ctx, _ = world(antibiotic(checked={(TODAY, time(8, 30))}))

        [readout] = await dose_status_readouts(ctx)

        assert readout.kind == "dose_status" and readout.authored_by == "code"
        assert readout.title == "항생제"
        assert readout.body == "마지막 체크 08:30 회차 · 다음 예정 12:30 · 오늘 체크 1/3회"

    async def test_체크_안_된_회차를_안_먹었다고_하지_않는다(self):
        ctx, _ = world(antibiotic())

        [readout] = await dose_status_readouts(ctx)

        for phrase in ("안 먹", "빼먹", "못 먹", "놓쳤", "누락"):
            assert phrase not in readout.body

    async def test_값이_없는_칸은_대시(self):
        ctx, _ = world(antibiotic(), now=datetime(2026, 9, 28, 20, 0, tzinfo=KST))

        [readout] = await dose_status_readouts(ctx)

        assert readout.body == "마지막 체크 - 회차 · 다음 예정 9/29 08:30 · 오늘 체크 0/3회"

    async def test_다른_날의_시각은_날짜를_붙인다(self):
        yesterday = TODAY - timedelta(days=1)
        ctx, _ = world(antibiotic(checked={(yesterday, time(19, 30))}))

        [readout] = await dose_status_readouts(ctx)

        assert readout.body.startswith("마지막 체크 9/27 19:30 회차")

    async def test_허가_문구가_없다(self):
        ctx, _ = world(antibiotic(checked={(TODAY, time(8, 30))}))

        [readout] = await dose_status_readouts(ctx)

        for phrase in ("먹여도 돼", "먹이면 안", "먹일 수", "가능", "괜찮"):
            assert phrase not in readout.body

    async def test_복약이_없으면_없음_안내(self):
        ctx, _ = world()

        [readout] = await dose_status_readouts(ctx)

        assert readout.body == READOUTS.render("notice.no_active_med").body


class TestSearchMedicationSchedules:
    async def test_진행_중_코스를_원문_그대로_낸다(self):
        ctx, _ = world(antibiotic())

        result = await search_medication_schedules(ctx, SearchMedicationArgs())

        assert result.success and result.operation == "query"
        [row] = result.data["courses"]
        assert row == {
            "title": "항생제",
            "dosage": "5ml",
            "notice_times": ["08:30", "12:30", "19:30"],
            "starts_on": "2026-09-27",
            "ends_on": "2026-09-29",
        }

    async def test_id_를_싣지_않는다(self):
        # 대상은 title_span 으로 코드가 찾는다 — 모델이 id 를 쓰지 않게
        course, rows = antibiotic()
        ctx, _ = world((course, rows))

        result = await search_medication_schedules(ctx, SearchMedicationArgs())

        assert str(course.id) not in json.dumps(result.data)

    async def test_진행_중_복약이_없으면_빈_목록(self):
        ctx, _ = world()

        result = await search_medication_schedules(ctx, SearchMedicationArgs())

        assert result.success and result.data["courses"] == []

    async def test_끝났거나_멈춘_코스는_안_나온다(self):
        ended = course_with_doses(
            "시럽", PATTERN, TODAY - timedelta(days=5), TODAY - timedelta(days=1)
        )
        stopped = antibiotic(status="stopped")
        ctx, _ = world(ended, stopped)

        result = await search_medication_schedules(ctx, SearchMedicationArgs())

        assert result.data["courses"] == []
