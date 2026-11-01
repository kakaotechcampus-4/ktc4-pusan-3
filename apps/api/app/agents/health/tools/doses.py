"""다음 예정 · 체크 안내 · 진행 중 코스 조회 — 복용 체크는 보호자가 화면에서 한다.

Agent 는 체크를 읽기만 한다.

복용 기록은 `medication_dose` 의 회차 행(`checked_at`)이다. Agent 는 그 칸을 쓰지 않는다.

    - "먹였어" · "못 먹였어" 에는 아무것도 쓰지 않고 복약 화면에서 체크해 달라고만 안내한다
    - 체크 안 된 회차는 "안 먹였다" 가 아니라 "체크 안 됨" 이다. 어떤 출력에도 "안 먹였어요" ·
      "빼먹었어요" 로 쓰지 않는다. 숫자는 `n/m회` 사실만 낸다
    - "마지막 체크" 는 체크를 누른 시각(`checked_at`)이 아니라 **회차 예정 시각**이다. 몰아서 누르면
      실제와 어긋난다 (6번 열 타임라인과 같은 기준)
    - `can_take` 같은 허가 필드 · 문구가 없다. 용량 · 간격 판단은 하지 않는다
"""

from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import UUID

from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.health.context import HealthContext
from app.agents.health.readouts import (
    DOSE_CHECK_ON_SCREEN,
    DOSE_STATUS,
    MED_ASK_PRESCRIBER,
    NOTICE_NO_ACTIVE_MED,
    READOUTS,
)
from app.agents.health.result import ToolResult, ok
from app.agents.health.schemas.medication import (
    DeferDoseQuestionArgs,
    SearchMedicationArgs,
    ShowDoseStatusArgs,
)
from app.agents.health.store.ports import MedicationCourse, MedicationDose

# "먹였어" 로 읽는 표현. 걸리면 모델을 부르지 않고 체크 안내만 낸다
DOSE_WORDS: tuple[str, ...] = ("먹였어", "먹였다", "먹였는데", "먹였어요", "못 먹였", "안 먹였")

_NONE = "-"


@dataclass(frozen=True)
class DoseStatus:
    """진행 중 코스 하나의 체크 현황. 허가 · 간격 같은 판단 필드를 두지 않는다."""

    schedule_id: UUID  # 어느 코스의 현황인가 — 같은 이름의 코스 둘이 같은 값으로 합쳐지지 않게
    title: str
    last_checked: datetime | None  # 체크된 회차 중 가장 늦은 것의 예정 시각. checked_at 이 아니다
    next_scheduled: datetime | None  # 지금 이후 가장 이른 회차
    checked_today: int  # 오늘 날짜 회차 중 checked_at 이 있는 것
    planned_today: int


async def get_next_dose(context: HealthContext) -> tuple[DoseStatus, ...]:
    """진행 중 코스마다 체크 현황. 복약이 없으면 빈 튜플이다."""
    courses = await _active(context)
    if not courses:
        return ()
    rows = await context.ports.medication.doses(
        schedule_ids=[course.id for course in courses],
        date_from=min(course.starts_on for course in courses),
        date_to=max(course.ends_on for course in courses),
    )
    return tuple(
        _status(context, course, [row for row in rows if row.schedule_id == course.id])
        for course in courses
    )


async def dose_status_readouts(context: HealthContext) -> list[Readout]:
    """`dose.status` — 코스마다 한 장(제목은 약 이름 원문). 복약이 없으면 없다는 안내 한 장."""
    statuses = await get_next_dose(context)
    if not statuses:
        return [READOUTS.render(NOTICE_NO_ACTIVE_MED)]
    return [
        replace(
            READOUTS.render(
                DOSE_STATUS,
                last=_when(status.last_checked, context.today),
                next=_when(status.next_scheduled, context.today),
                checked=status.checked_today,
                planned=status.planned_today,
            ),
            title=status.title,
            source_refs=(Ref("medication_schedule", status.schedule_id),),
        )
        for status in statuses
    ]


async def check_on_screen(context: HealthContext) -> Readout:
    """ "먹였어" 의 답. 쓰기 0 — 복약 화면에서 체크해 달라고만 안내한다.

    진행 중 복약이 없으면 체크할 회차도 없으니 없다는 안내를 낸다. 안내에는 진행 중 코스의 ref 가
    붙는다 — 화면이 그 코스를 가리킬 수 있게 하는 값이고, 체크할 수 없는 끝났거나 멈춘 코스는
    싣지 않는다. 어느 코스 얘기인지 가리는 것은 이 함수가 아니다(약 이름을 보지 않는다).
    다만 run 결과를 화면으로 옮기는 쪽이 아직 readout 의 ref 를 내보내지 않아서, 화면이 이 값을
    읽는 것은 그 길이 열린 뒤다.
    """
    courses = await _active(context)
    if not courses:
        return READOUTS.render(NOTICE_NO_ACTIVE_MED)
    return replace(
        READOUTS.render(DOSE_CHECK_ON_SCREEN),
        source_refs=tuple(Ref("medication_schedule", course.id) for course in courses),
    )


async def show_dose_status(context: HealthContext, args: ShowDoseStatusArgs) -> ToolResult:
    """읽기 전용 출력 tool — 표에 안 걸린 현황 질문("오늘 몇 번 먹였지?")의 출구.

    내용은 `dose_status_readouts` 가 낸다(모델이 쓰지 않는다). 모델에게 돌아가는 것은 더한
    readout 개수뿐이다 — 약 이름 · 용량은 외부 모델로 보내지 않는다.
    """
    return ok(
        "query",
        "show_dose_status",
        shown=context.state.add_once(await dose_status_readouts(context)),
    )


async def defer_dose_question(context: HealthContext, args: DeferDoseQuestionArgs) -> ToolResult:
    """읽기 전용 출력 tool — 용량 · 간격을 바꿔도 되는지 묻기만 한 말의 출구.

    아무것도 읽지 않는다. 되는지 안 되는지는 판단하지 않고 처방한 곳에 확인하도록만 안내한다.
    """
    context.state.add_once([READOUTS.render(MED_ASK_PRESCRIBER)])
    return ok("query", "defer_dose_question")


async def search_medication_schedules(
    context: HealthContext, args: SearchMedicationArgs
) -> ToolResult:
    """진행 중 코스. 약 이름 · 용량은 원문 그대로이고 id 는 싣지 않는다 — 대상은 title_span 으로
    코드가 찾는다. 진료 준비 정리(6번)도 같은 tool 을 연다."""
    courses = await _active(context)
    return ok(
        "query",
        "search_medication_schedules",
        courses=[
            {
                "title": course.title,
                "dosage": course.dosage,
                "notice_times": sorted(f"{slot.scheduled_time:%H:%M}" for slot in course.pattern),
                "starts_on": course.starts_on.isoformat(),
                "ends_on": course.ends_on.isoformat(),
            }
            for course in courses
        ],
    )


def _status(
    context: HealthContext, course: MedicationCourse, rows: list[MedicationDose]
) -> DoseStatus:
    checked = [_moment(context, row) for row in rows if row.checked_at is not None]
    upcoming = [_moment(context, row) for row in rows if _moment(context, row) > context.now]
    today_rows = [row for row in rows if row.dose_date == context.today]
    return DoseStatus(
        schedule_id=course.id,
        title=course.title,
        last_checked=max(checked, default=None),
        next_scheduled=min(upcoming, default=None),
        checked_today=sum(1 for row in today_rows if row.checked_at is not None),
        planned_today=len(today_rows),
    )


def _moment(context: HealthContext, row: MedicationDose) -> datetime:
    """회차의 예정 시각 — 날짜(KST) + 시각을 아이 타임존으로."""
    return datetime.combine(row.dose_date, row.scheduled_time, tzinfo=context.timezone)


def _when(moment: datetime | None, today: date) -> str:
    """오늘이면 시각만, 다른 날이면 날짜를 붙인다. 값이 없으면 '-' (짐작해 채우지 않는다)."""
    if moment is None:
        return _NONE
    if moment.date() == today:
        return f"{moment:%H:%M}"
    return f"{moment.month}/{moment.day} {moment:%H:%M}"


async def _active(context: HealthContext) -> list[MedicationCourse]:
    return await context.ports.medication.active_courses(
        child_id=context.child_id, today=context.today
    )
