"""복약 두 표 — DB 가 계산하고 막는 것 (#537 · docs/medication/medication-tables-v1.md).

CHECK 위반은 raw SQL 로 넣는다. ORM 으로 넣으면 enum 칸은 SQLAlchemy 가 먼저 거절해서
DB 의 CHECK 가 실제로 있는지 확인하지 못한다.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.models import (
    MedicationDose,
    MedicationSchedule,
    MedicationStatus,
    TimingAnchor,
    in_progress,
)

STARTS = date(2026, 10, 20)
KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


async def add_schedule(session, child, **over) -> MedicationSchedule:
    values = {
        "child_id": child.id,
        "title": "해열제",
        "starts_on": STARTS,
        "ends_on": STARTS + timedelta(days=2),
        "created_by": "caregiver",
    }
    schedule = MedicationSchedule(**values | over)
    session.add(schedule)
    await session.flush()
    return schedule


async def scheduled_times(session, schedule_id) -> list[time]:
    rows = await session.execute(
        select(MedicationDose.scheduled_time)
        .where(MedicationDose.schedule_id == schedule_id)
        .order_by(MedicationDose.dose_date, MedicationDose.scheduled_time)
    )
    return list(rows.scalars())


# ── 1. 기준 시각 계산 ─────────────────────────────────────────────────────


async def test_하루_세_번_식후_3일이면_회차_9행(session, child):
    schedule = await add_schedule(session, child)
    for day in range(3):
        for anchor in ("breakfast", "lunch", "dinner"):
            session.add(
                MedicationDose(
                    schedule_id=schedule.id,
                    dose_date=STARTS + timedelta(days=day),
                    timing_anchor=anchor,
                    offset_minutes=30,
                )
            )
    await session.flush()

    times = await scheduled_times(session, schedule.id)
    assert times == [time(8, 30), time(12, 30), time(17, 30)] * 3


@pytest.mark.parametrize(
    ("anchor", "offset", "expected"),
    [
        ("wake_up", 0, time(7, 30)),
        ("breakfast", 0, time(8, 0)),
        ("lunch", 0, time(12, 0)),
        ("dinner", 0, time(17, 0)),
        ("bedtime", 0, time(20, 30)),
        ("breakfast", -30, time(7, 30)),  # 식전
        ("wake_up", -120, time(5, 30)),  # 가장 이른 시각
        ("bedtime", 180, time(23, 30)),  # 가장 늦은 시각 — 자정을 넘지 않는다
    ],
)
async def test_기준_시각에_오프셋을_더한다(session, child, anchor, offset, expected):
    schedule = await add_schedule(session, child)
    session.add(
        MedicationDose(
            schedule_id=schedule.id, dose_date=STARTS, timing_anchor=anchor, offset_minutes=offset
        )
    )
    await session.flush()
    assert await scheduled_times(session, schedule.id) == [expected]


async def test_fixed_는_fixed_time_이_곧_알림_시각(session, child):
    schedule = await add_schedule(session, child)
    session.add(
        MedicationDose(
            schedule_id=schedule.id,
            dose_date=STARTS,
            timing_anchor=TimingAnchor.FIXED,
            fixed_time=time(19, 30),
        )
    )
    await session.flush()
    assert await scheduled_times(session, schedule.id) == [time(19, 30)]


async def test_기본값(session, child):
    schedule = await add_schedule(session, child)
    await session.refresh(schedule)
    assert schedule.status == MedicationStatus.ACTIVE
    assert schedule.source == "utterance"
    assert schedule.symptom_codes == []
    assert schedule.source_draft_id is None
    assert schedule.note is None


# ── 2. CHECK ──────────────────────────────────────────────────────────────

SCHEDULE_SQL = """
    INSERT INTO medication_schedule
      (child_id, title, starts_on, ends_on, status, created_by, source, storage, med_form)
    VALUES
      (:child_id, :title, :starts_on, :ends_on, :status, :created_by, :source, :storage, :med_form)
    RETURNING id
"""

DOSE_SQL = """
    INSERT INTO medication_dose
      (schedule_id, dose_date, timing_anchor, offset_minutes, fixed_time)
    VALUES (:schedule_id, :dose_date, :timing_anchor, :offset_minutes, :fixed_time)
"""


async def insert_schedule(session, child, **over):
    values = {
        "child_id": child.id,
        "title": "해열제",
        "starts_on": STARTS,
        "ends_on": STARTS,
        "status": "active",
        "created_by": "caregiver",
        "source": "utterance",
        "storage": None,
        "med_form": None,
    }
    return (await session.execute(text(SCHEDULE_SQL), values | over)).scalar_one()


async def insert_dose(session, schedule_id, **over):
    values = {
        "schedule_id": schedule_id,
        "dose_date": STARTS,
        "timing_anchor": "breakfast",
        "offset_minutes": 0,
        "fixed_time": None,
    }
    await session.execute(text(DOSE_SQL), values | over)


@pytest.mark.parametrize(
    "bad",
    [
        {"ends_on": STARTS - timedelta(days=1)},  # 끝이 시작보다 앞
        {"ends_on": None},  # 무기한 코스 없음
        {"status": "completed"},  # 끝남은 ends_on 으로 계산한다
        {"status": "paused"},
        {"created_by": "doctor"},
        {"source": "photo"},
        {"storage": "freezer"},
        {"med_form": "syrup"},
        {"title": None},
    ],
)
async def test_medication_schedule_이_막는_값(session, child, bad):
    with pytest.raises(IntegrityError):
        await insert_schedule(session, child, **bad)


async def test_medication_schedule_이_받는_값(session, child):
    await insert_schedule(session, child, status="stopped", storage="fridge", med_form="drops")
    await insert_schedule(session, child, source="prescription_ocr", created_by="agent")


@pytest.mark.parametrize(
    "bad",
    [
        {"timing_anchor": "fixed"},  # fixed 인데 fixed_time 없음
        {"fixed_time": time(8, 30)},  # fixed 가 아닌데 fixed_time 있음
        {"timing_anchor": "fixed", "fixed_time": time(8, 30), "offset_minutes": 10},
        {"offset_minutes": -121},
        {"offset_minutes": 181},
        {"timing_anchor": "noon"},
        {"timing_anchor": None},
        {"offset_minutes": None},
        {"dose_date": None},
    ],
)
async def test_medication_dose_가_막는_값(session, child, bad):
    schedule_id = await insert_schedule(session, child)
    with pytest.raises(IntegrityError):
        await insert_dose(session, schedule_id, **bad)


@pytest.mark.parametrize("offset", [-120, 180])
async def test_오프셋_경계는_통과한다(session, child, offset):
    schedule_id = await insert_schedule(session, child)
    await insert_dose(session, schedule_id, offset_minutes=offset)


# ── 3. UNIQUE ─────────────────────────────────────────────────────────────


async def test_같은_날_같은_시각은_기준이_달라도_한_번(session, child):
    schedule_id = await insert_schedule(session, child)
    await insert_dose(session, schedule_id, offset_minutes=30)  # breakfast + 30 = 08:30
    with pytest.raises(IntegrityError):
        await insert_dose(session, schedule_id, timing_anchor="fixed", fixed_time=time(8, 30))


async def test_다른_날이면_같은_시각을_둘_수_있다(session, child):
    schedule_id = await insert_schedule(session, child, ends_on=STARTS + timedelta(days=1))
    await insert_dose(session, schedule_id, offset_minutes=30)
    await insert_dose(
        session,
        schedule_id,
        dose_date=STARTS + timedelta(days=1),
        timing_anchor="fixed",
        fixed_time=time(8, 30),
    )


# ── 4. CASCADE ────────────────────────────────────────────────────────────


async def count(session, table: str) -> int:
    return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_아이를_지우면_코스와_회차가_함께_지워진다(session, child):
    schedule_id = await insert_schedule(session, child)
    await insert_dose(session, schedule_id)
    await session.execute(text("DELETE FROM child WHERE id = :id"), {"id": child.id})
    assert await count(session, "medication_schedule") == 0
    assert await count(session, "medication_dose") == 0


async def test_코스를_지우면_회차가_함께_지워진다(session, child):
    schedule_id = await insert_schedule(session, child)
    await insert_dose(session, schedule_id)
    await session.execute(
        text("DELETE FROM medication_schedule WHERE id = :id"), {"id": schedule_id}
    )
    assert await count(session, "medication_dose") == 0


# ── 5. 진행 중 코스 ───────────────────────────────────────────────────────


async def test_진행_중은_active_이면서_오늘_끝나지_않은_코스(session, child):
    today = date(2026, 10, 21)
    await add_schedule(session, child, title="오늘 끝남", ends_on=today)
    await add_schedule(session, child, title="내일 끝남", ends_on=today + timedelta(days=1))
    await add_schedule(session, child, title="어제 끝남", ends_on=today - timedelta(days=1))
    await add_schedule(
        session,
        child,
        title="중단",
        ends_on=today + timedelta(days=1),
        status=MedicationStatus.STOPPED,
    )

    titles = await session.execute(
        select(MedicationSchedule.title)
        .where(MedicationSchedule.child_id == child.id, in_progress(today))
        .order_by(MedicationSchedule.title)
    )
    assert list(titles.scalars()) == ["내일 끝남", "오늘 끝남"]


async def test_진행_중_판정은_넘겨받은_오늘을_쓴다(session, child):
    # DB 의 CURRENT_DATE(UTC)가 아니라 인자로 받은 KST 오늘이 기준이다
    await add_schedule(session, child, ends_on=date(2000, 1, 1), starts_on=date(2000, 1, 1))
    found = await session.execute(
        select(func.count())
        .select_from(MedicationSchedule)
        .where(MedicationSchedule.child_id == child.id, in_progress(date(2000, 1, 1)))
    )
    assert found.scalar_one() == 1


# ── 6. 시간대 ─────────────────────────────────────────────────────────────
# DB 세션은 UTC 다(배포 db 컨테이너에도 TZ 가 없다). 날짜는 KST 로 구해서 넘긴다.


async def test_KST_자정_직후에는_어제_끝난_코스가_진행_중이_아니다(session, child):
    await add_schedule(session, child, starts_on=STARTS, ends_on=date(2026, 10, 21))
    now = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)  # KST 10/22 00:30

    async def count(today: date) -> int:
        found = await session.execute(
            select(func.count())
            .select_from(MedicationSchedule)
            .where(MedicationSchedule.child_id == child.id, in_progress(today))
        )
        return found.scalar_one()

    assert await count(now.astimezone(KST).date()) == 0
    assert await count(now.date()) == 1  # UTC 날짜를 넘기면 끝난 약이 '복용 중' 으로 잡힌다


async def test_checked_at_은_같은_순간으로_돌아온다(session, child):
    schedule = await add_schedule(session, child)
    checked = datetime(2026, 10, 21, 7, 0, tzinfo=KST)  # = UTC 10/20 22:00
    dose = MedicationDose(
        schedule_id=schedule.id,
        dose_date=date(2026, 10, 21),
        timing_anchor="breakfast",
        offset_minutes=30,
        checked_at=checked,
    )
    session.add(dose)
    await session.flush()

    back = await session.scalar(
        select(MedicationDose.checked_at).where(MedicationDose.id == dose.id)
    )
    assert back == checked
    assert back.astimezone(KST).date() == date(2026, 10, 21)


async def test_SQL_에서_체크한_날은_AT_TIME_ZONE_으로_구한다(session, child):
    schedule = await add_schedule(session, child)
    dose = MedicationDose(
        schedule_id=schedule.id,
        dose_date=date(2026, 10, 21),
        timing_anchor="breakfast",
        offset_minutes=30,
        checked_at=datetime(2026, 10, 21, 7, 0, tzinfo=KST),
    )
    session.add(dose)
    await session.flush()
    await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))

    plain, kst = (
        await session.execute(
            text(
                "SELECT checked_at::date, (checked_at AT TIME ZONE 'Asia/Seoul')::date "
                "FROM medication_dose WHERE id = :id"
            ),
            {"id": dose.id},
        )
    ).one()
    assert kst == date(2026, 10, 21)
    assert plain == date(2026, 10, 20)  # ::date 는 세션 시간대(UTC) 날짜라 하루 앞이다
