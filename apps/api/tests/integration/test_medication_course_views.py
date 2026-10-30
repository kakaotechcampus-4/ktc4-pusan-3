"""진행 중인 복약 코스 조회 — `service.list_in_progress` · `course_views` (#577).

진행 중 판정은 `models.in_progress` 한 곳이고 여기서 다시 쓰지 않는다. 각 코스는 알림 시각 목록과
**오늘까지**의 회차만 들고 온다 — 아직 오지 않은 날의 회차는 싣지 않는다.

회차 행은 repository 로 직접 넣는다 — 서비스는 지난 날짜의 회차를 만들지 않아서 "이미 지난 날의
회차가 있는 코스" 를 서비스로는 만들 수 없다.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import update

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.medication.agent_reader import pattern_of
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSource,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_doses, stop_schedule
from app.domains.medication.service import course_views, list_in_progress

TODAY = date(2026, 10, 27)
MEALS = (TimingAnchor.BREAKFAST, TimingAnchor.LUNCH, TimingAnchor.DINNER)


@pytest.fixture
async def child(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


def meals(first: date, last: date, anchors=MEALS) -> list[NewDose]:
    """first~last 의 날마다 anchors 각각 +30분."""
    days = (last - first).days + 1
    return [
        NewDose(first + timedelta(days=n), anchor, 30, None)
        for n in range(days)
        for anchor in anchors
    ]


async def seed(session, child, *, title="약", starts=-1, ends=1, doses=None, **over):
    return await add_course(
        session,
        child_id=child.id,
        title=title,
        starts_on=day(starts),
        ends_on=day(ends),
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=meals(day(starts), day(ends)) if doses is None else doses,
        **over,
    )


async def titles(session, child, today=TODAY) -> list[str]:
    return [v.course.title for v in await list_in_progress(session, child_id=child.id, today=today)]


# ── 1. 어느 코스가 진행 중인가 ───────────────────────────────────────────


async def test_진행_중인_코스만_온다(session, child):
    await seed(session, child, title="진행", starts=-2, ends=2)
    stopped = await seed(session, child, title="멈춤", starts=-2, ends=2)
    await stop_schedule(
        session, child_id=child.id, schedule_id=stopped.id, stopped_at=datetime.now(UTC)
    )
    await seed(session, child, title="어제 끝남", starts=-5, ends=-1)
    await seed(session, child, title="내일 시작", starts=1, ends=3)

    assert await titles(session, child) == ["진행"]


async def test_시작일이_오늘이거나_끝일이_오늘이면_진행_중이다(session, child):
    await seed(session, child, title="오늘 시작", starts=0, ends=3)
    await seed(session, child, title="오늘 끝", starts=-3, ends=0)

    assert sorted(await titles(session, child)) == ["오늘 끝", "오늘 시작"]


async def test_코스가_없으면_빈_목록(session, child):
    assert await list_in_progress(session, child_id=child.id, today=TODAY) == []


async def test_다른_아이의_코스는_오지_않는다(session, child):
    other = Child(
        owner_parent_id=child.owner_parent_id, nickname="other", birth_date=date(2022, 1, 1)
    )
    session.add(other)
    await session.flush()
    await seed(session, other, title="남의 약")

    assert await titles(session, child) == []


async def test_날짜가_바뀌면_진행_여부도_바뀐다(session, child):
    await seed(session, child, title="약", starts=0, ends=1)

    assert await titles(session, child, today=day(-1)) == []  # 하루 전엔 시작 전
    assert await titles(session, child, today=day(1)) == ["약"]
    assert await titles(session, child, today=day(2)) == []  # 이틀 뒤엔 끝남


# ── 2. 순서 ──────────────────────────────────────────────────────────────


async def test_시작일_순이고_같으면_만든_순이다(session, child):
    await seed(session, child, title="늦게 시작", starts=0, ends=3)
    await seed(session, child, title="먼저 시작", starts=-3, ends=3)
    await seed(session, child, title="같은 날 둘째", starts=0, ends=3)

    assert await titles(session, child) == ["먼저 시작", "늦게 시작", "같은 날 둘째"]


# ── 3. 회차 ──────────────────────────────────────────────────────────────


async def test_회차는_오늘까지만_싣고_날짜_시각_순이다(session, child):
    await seed(session, child, starts=-2, ends=2)

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert [(d.dose_date, d.scheduled_time) for d in view.doses] == [
        (day(n), time(h, 30)) for n in (-2, -1, 0) for h in (8, 12, 19)
    ]


async def test_오늘_회차는_싣고_내일_회차는_싣지_않는다(session, child):
    await seed(session, child, starts=0, ends=1)

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert {d.dose_date for d in view.doses} == {TODAY}


async def test_체크한_회차는_체크_시각이_그대로_온다(session, child):
    course = await seed(session, child, starts=0, ends=0)
    checked_at = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)
    await session.execute(
        update(MedicationDose)
        .where(
            MedicationDose.schedule_id == course.id, MedicationDose.scheduled_time == time(8, 30)
        )
        .values(checked_at=checked_at)
    )

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert [d.checked_at for d in view.doses] == [checked_at, None, None]


async def test_시작_전_날짜만_있는_코스는_회차가_비어_있다(session, child):
    await seed(
        session, child, starts=0, ends=0, doses=[NewDose(day(1), TimingAnchor.LUNCH, 0, None)]
    )

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert view.doses == []
    assert view.notice_times == ["12:00"]  # 회차가 아직 안 와도 알림 시각은 안다


async def test_긴_코스는_오늘까지의_회차를_전부_싣는다(session, child):
    # 알려진 한계 — 90일 × 하루 3번이면 한 코스에 270개까지 늘어난다 (docs/api/medication-v1.md §8)
    await seed(session, child, starts=-10, ends=3)

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert len(view.doses) == 33  # 11일 × 3


async def test_코스마다_자기_회차만_든다(session, child):
    lunch = await seed(
        session,
        child,
        title="점심약",
        starts=-1,
        ends=0,
        doses=meals(day(-1), day(0), (TimingAnchor.LUNCH,)),
    )
    dinner = await seed(
        session,
        child,
        title="저녁약",
        starts=-1,
        ends=0,
        doses=meals(day(-1), day(0), (TimingAnchor.DINNER,)),
    )

    views = await list_in_progress(session, child_id=child.id, today=TODAY)

    by_id = {v.course.id: v for v in views}
    assert {d.schedule_id for d in by_id[lunch.id].doses} == {lunch.id}
    assert {d.schedule_id for d in by_id[dinner.id].doses} == {dinner.id}
    assert by_id[lunch.id].notice_times == ["12:30"]
    assert by_id[dinner.id].notice_times == ["19:30"]


# ── 4. 알림 시각 ─────────────────────────────────────────────────────────


async def test_알림_시각은_중복_없이_오름차순_HHMM(session, child):
    await seed(
        session,
        child,
        starts=-2,
        ends=2,
        doses=meals(day(-2), day(2), (TimingAnchor.DINNER, TimingAnchor.BREAKFAST)),
    )

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert view.notice_times == ["08:30", "19:30"]


async def test_알림_시각은_가장_마지막_날의_패턴이다_옛_패턴의_지난_회차는_섞이지_않는다(
    session, child
):
    # 수정하면 이미 지난 날의 회차는 옛 패턴으로 남는다 — 하루에 울릴 시각은 지금 패턴이어야 한다
    old = [fx for fx in meals(day(-1), day(-1), (TimingAnchor.DINNER,))]
    new = meals(day(0), day(2), (TimingAnchor.LUNCH,))
    await seed(session, child, starts=-1, ends=2, doses=old + new)

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert view.notice_times == ["12:30"]
    assert len(view.doses) == 2  # 어제 저녁 · 오늘 점심 — 회차 기록은 옛 패턴도 그대로 보인다


async def test_회차가_하나도_없는_코스의_알림_시각은_빈_목록(session, child):
    await seed(session, child, starts=0, ends=0, doses=[])

    (view,) = await list_in_progress(session, child_id=child.id, today=TODAY)

    assert view.notice_times == []


# ── 5. 코스 목록을 직접 넘길 때 ──────────────────────────────────────────


async def test_course_views_는_넘긴_코스_순서를_그대로_지킨다(session, child):
    first = await seed(session, child, title="첫째")
    second = await seed(session, child, title="둘째")

    views = await course_views(session, [second, first], today=TODAY)

    assert [v.course.title for v in views] == ["둘째", "첫째"]


async def test_course_views_는_빈_목록이면_조회하지_않고_빈_목록(session):
    assert await course_views(session, [], today=TODAY) == []


def fixed(offset: int, *at: tuple[int, int]) -> list[NewDose]:
    return [NewDose(day(offset), TimingAnchor.FIXED, 0, time(h, m)) for h, m in at]


OLD = ((8, 30), (12, 30), (19, 30))


@pytest.mark.parametrize(
    ("doses", "expected"),
    [
        pytest.param(  # 어제 · 오늘 · 내일 — 오늘 09:40 에 09:00 · 20:00 으로 고친 뒤
            fixed(-1, *OLD) + fixed(0, (8, 30), (20, 0)) + fixed(1, (9, 0), (20, 0)),
            ["09:00", "20:00"],
            id="수정한_코스는_오늘_다음_날의_새_시각",
        ),
        pytest.param(  # 마지막 날이 오늘이라 다음 날이 없다 — 옛 시각이 섞인다 (알려진 경계)
            fixed(-1, *OLD) + fixed(0, (8, 30), (20, 0)),
            ["08:30", "20:00"],
            id="마지막_날이_오늘이면_오늘_행을_읽는다",
        ),
        pytest.param(fixed(2, (9, 0)) + fixed(3, (9, 0)), ["09:00"], id="시작_전_코스"),
        pytest.param(
            fixed(-1, *OLD) + fixed(0, *OLD) + fixed(1, *OLD),
            ["08:30", "12:30", "19:30"],
            id="고치지_않은_코스",
        ),
    ],
)
async def test_화면의_알림_시각과_Agent_패턴은_같은_날을_읽어_같다(session, child, doses, expected):
    first = min(d.dose_date for d in doses)
    last = max(d.dose_date for d in doses)
    course = await add_course(
        session,
        child_id=child.id,
        title="약",
        starts_on=first,
        ends_on=last,
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=doses,
    )

    (view,) = await course_views(session, [course], today=TODAY)
    rows = await list_doses(session, schedule_ids=[course.id])
    agent = [f"{slot.scheduled_time:%H:%M}" for slot in pattern_of(rows, basis=TODAY)]

    assert view.notice_times == expected
    assert agent == expected
