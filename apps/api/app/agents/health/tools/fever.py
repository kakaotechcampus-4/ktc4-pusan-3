"""열 타임라인 `build_fever_timeline` · 응급 판정 `infant_fever` — 모델 0회.

체온 · 체크된 복약 회차 · 보호자가 기록한 먹인 약을 나열만 한다. 해석하지 않는다.

- 구간은 `now` 로부터 48시간. 시각은 `measured_at` → `observed_time` 순이고 아이 타임존으로 보인다
  (DB 의 UTC 오프셋 그대로 찍으면 자정 부근 날짜가 어긋난다). 시각을 말하지 않은 기록은 날짜만
  보이고 그날 끝에 놓는다
- 체온 기록이 없으면 `fever.no_record` 한 줄이다. 복약만 나열하지 않는다
- 마지막 복약은 코스마다 `checked_at` 이 있는 가장 늦은 **회차의 예정 시각**이다(지금까지 온
  회차만 — 미리 누른 미래 회차는 아니다). 체크를 누른 시각(`checked_at`)은 쓰지 않는다 — 몰아서
  누르면 실제와 어긋난다. 체크 안 된 회차는 "체크 안 됨" 이지 "안 먹였다" 가 아니라서 나열하지
  않는다
- 다음 예정은 등록된 회차 행 중 체크 안 된 것에서만 나온다. 패턴으로 계산하지 않고, 멈춘 코스의
  행은 계획이 아니다
- 먹인 약(`medication_given`)은 보호자가 말한 시각(`observed_time`)순이다. 연결된 증상
  (`linked_symptom_id`)이 체온 줄로 보이면 그 줄 바로 아래에 묶음으로만 놓고, 문장으로 잇지 않는다
- 약 성분 · 계열 · "투약 가능 시각" · 경과 해석을 내지 않는다. 약 이름은 보호자가 말한 원문이다
- 로그에 원문을 싣지 않는다 — 이 파일은 로그를 남기지 않는다

`infant_fever` 는 3개월 미만 + 48시간 안 발열이면 참이다. 응급 안내(`emergency`)를 타임라인보다
먼저 내는 것은 부르는 쪽(라벨)이다. 월령 · 체온 기준은 `reference/infant_fever.yaml` 한 곳이다 —
의료 검수 전이지만 켜 둔다(끄면 응급 안내가 사라진다. 그 파일 머리말).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from uuid import UUID

from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.health.context import HealthContext
from app.agents.health.readouts import (
    FEVER_GIVEN_DOSE,
    FEVER_LAST_DOSE,
    FEVER_LINE,
    FEVER_LOW,
    FEVER_NEXT_DOSE,
    FEVER_NO_RECORD,
    FEVER_PEAK,
    READOUTS,
)
from app.agents.health.reference import infant_fever_rule
from app.agents.health.result import ToolResult, ok
from app.agents.health.schemas.records import ShowFeverTimelineArgs
from app.agents.health.store.ports import HealthObservation, MedicationCourse, MedicationDose
from app.agents.health.tools.symptoms import observation_days, symptom_codes_of
from app.agents.health.when import dose_moment, md, md_time

WINDOW = timedelta(hours=48)

# 응급 안내 기준 — 값과 출처 · 검수 상태는 reference/infant_fever.yaml 이 정본이다 (#551).
# 부위별 기준값은 두지 않는다 — 출처 검수(H-1)에서 정한다
_RULE = infant_fever_rule()
FEVER_TEMPERATURE = _RULE.fever_temperature  # 발열로 보는 체온
INFANT_FEVER_BELOW_MONTH = _RULE.below_month  # 이 월령 미만의 발열은 응급 안내가 먼저다

_SITE_LABEL = {
    "ear": "귀",
    "forehead": "이마",
    "armpit": "겨드랑이",
    "oral": "입",
    "rectal": "항문",
}


@dataclass(frozen=True)
class _Event:
    """시간 흐름에 놓일 기록 하나. `day` 와 `moment` 로 정렬한다 — 시각이 없으면 그날 끝이다."""

    row: HealthObservation
    day: date
    moment: datetime | None

    @property
    def key(self) -> tuple[date, int, datetime | None]:
        return (self.day, 1 if self.moment is None else 0, self.moment)

    def label(self) -> str:
        if self.moment is None:
            return md(self.day)
        return md_time(self.moment)


def build_fever_timeline(
    observations: Sequence[HealthObservation],
    courses: Sequence[MedicationCourse],
    doses: Sequence[MedicationDose],
    *,
    now: datetime,
    timezone: tzinfo,
) -> tuple[Readout, ...]:
    """최근 48시간의 체온 · 마지막 복약 · 다음 예정 · 먹인 약. 줄 하나가 `Readout` 하나다.

    `courses` 는 48시간과 겹치는 코스(어제 끝난 코스 포함) · `doses` 는 그 코스들의 회차 행이다.
    체온 기록이 없으면 `fever.no_record` 한 줄만 낸다.
    """
    start = now - WINDOW
    temperatures = _events((o for o in observations if o.temperature is not None), start, timezone)
    if not temperatures:
        return (READOUTS.render(FEVER_NO_RECORD),)

    given = _events(
        (o for o in observations if o.medication_given is not None),
        start,
        timezone,
        said_only=True,
    )
    shown = {e.row.id for e in temperatures}
    linked: dict[UUID, list[_Event]] = {}
    loose: list[_Event] = []
    for event in given:
        target = event.row.linked_symptom_id
        if target in shown:
            linked.setdefault(target, []).append(event)
        else:
            loose.append(event)

    lines: list[Readout] = []
    for event in _sorted([*temperatures, *loose]):
        if event.row.medication_given is not None:
            lines.append(_given_line(event))
            continue
        lines.append(_temperature_line(event))
        lines.extend(_given_line(g) for g in linked.get(event.row.id, ()))

    # 같은 값이면 먼저 잰 것을 고른다. `temperatures` 가 시간순이라 max · min 이 첫 것을 돌려준다
    peak = max(temperatures, key=lambda e: _value(e))
    lines.append(_extreme(FEVER_PEAK, peak))
    if len(temperatures) > 1:
        lines.append(_extreme(FEVER_LOW, min(temperatures, key=lambda e: _value(e))))

    lines.extend(_last_doses(courses, doses, start.astimezone(timezone).date(), now, timezone))
    lines.extend(_next_dose(courses, doses, now, timezone))
    return tuple(lines)


async def fever_readouts(
    context: HealthContext, observations: Sequence[HealthObservation]
) -> list[Readout]:
    """최근 48시간의 열 타임라인. 코스는 그 구간과 겹치는 것(어제 끝난 것 포함), 회차는 앞날까지.

    열 표현 표에 걸린 질문(라벨)과 표에 안 걸린 열 질문의 출구(`show_fever_timeline`)가 같이 쓴다.
    """
    first_day = _window_first_day(context)
    medication = context.ports.medication
    courses = await medication.courses_overlapping(
        child_id=context.child_id, date_from=first_day, date_to=context.today
    )
    # 다음 예정은 오늘 이후 회차 행에서 나온다 — 코스 마지막 날까지 읽는다
    last_day = max([context.today, *(course.ends_on for course in courses)])
    doses = (
        await medication.doses(
            schedule_ids=[course.id for course in courses], date_from=first_day, date_to=last_day
        )
        if courses
        else []
    )
    return list(
        build_fever_timeline(
            observations, courses, doses, now=context.now, timezone=context.timezone
        )
    )


async def show_fever_timeline(context: HealthContext, args: ShowFeverTimelineArgs) -> ToolResult:
    """읽기 전용 출력 tool — 표에 안 걸린 열 질문("열나는데 언제부터였지")의 출구.

    진료 준비 정리 대신 열 타임라인만 낸다. 건강 기록은 라벨이 이미 읽은 것이 있으면 그것을 쓰고
    다시 읽지 않는다. 응급 안내는 라벨이 맨 앞에 먼저 넣어 두므로 순서를 다루지 않는다. 모델에게
    돌아가는 것은 줄 수뿐이다 — 체온 · 약 이름은 외부 모델로 보내지 않는다.
    """
    state = context.state
    if state.visit_observations is None:
        # 라벨이 기록을 보관하기 전에 불렸다 — "기록 없음" 으로 읽히지 않게 48시간 창을 직접 읽는다
        state.visit_observations = tuple(
            await context.ports.memory.health_observations(
                child_id=context.child_id,
                date_from=_window_first_day(context),
                date_to=context.today,
            )
        )
    lines = state.add_once(await fever_readouts(context, state.visit_observations))
    return ok("query", "show_fever_timeline", lines=lines)


def _window_first_day(context: HealthContext) -> date:
    """48시간 창의 첫 날 — 아이 타임존의 달력 날짜."""
    return (context.now - WINDOW).astimezone(context.timezone).date()


def infant_fever(
    observations: Sequence[HealthObservation], *, months: int, now: datetime, timezone: tzinfo
) -> bool:
    """3개월 미만 + 최근 48시간 안 `fever` 코드 또는 `temperature >= FEVER_TEMPERATURE`.

    시각을 말하지 않은 기록은 날짜로 본다 — 48시간 경계 날짜까지 센다(응급 안내는 놓치는 쪽이 더
    위험하다). 기록 없음이 "열 없음" 은 아니지만 이 함수는 기록이 있을 때만 참이다.
    """
    if months >= INFANT_FEVER_BELOW_MONTH:
        return False
    start = now - WINDOW
    return any(
        _event(o, start, timezone) is not None
        and (
            "fever" in symptom_codes_of(o.symptoms)
            or (o.temperature is not None and o.temperature >= FEVER_TEMPERATURE)
        )
        for o in observations
    )


def _event(
    row: HealthObservation, start: datetime, timezone: tzinfo, *, said_only: bool = False
) -> _Event | None:
    """구간 안의 기록이면 `_Event`, 밖이면 None. 약 행은 말한 시각(`observed_time`)만 본다."""
    moment = row.observed_time if said_only else (row.measured_at or row.observed_time)
    if moment is not None:
        local = moment.astimezone(timezone)
        return _Event(row, local.date(), local) if moment >= start else None
    first_day = start.astimezone(timezone).date()
    days = observation_days(row, timezone)
    if days[-1] < first_day:
        return None
    return _Event(row, days[0], None)


def _events(
    rows: Iterable[HealthObservation], start: datetime, timezone: tzinfo, *, said_only: bool = False
) -> list[_Event]:
    """구간 안의 기록을 시간 흐름 순으로. 시각이 없는 기록은 그날 끝이다."""
    found = (_event(row, start, timezone, said_only=said_only) for row in rows)
    return _sorted(e for e in found if e is not None)


def _sorted(events: Iterable[_Event]) -> list[_Event]:
    return sorted(events, key=lambda e: e.key)


def _value(event: _Event) -> Decimal:
    assert event.row.temperature is not None
    return event.row.temperature


def _temperature_line(event: _Event) -> Readout:
    site = _SITE_LABEL.get(event.row.measure_site or "", "")
    return _with_ref(
        READOUTS.render(
            FEVER_LINE,
            time=event.label(),
            temperature=_value(event),
            site=f" ({site})" if site else "",
        ),
        Ref("observation_health", event.row.id),
    )


def _extreme(key: str, event: _Event) -> Readout:
    return _with_ref(
        READOUTS.render(key, temperature=_value(event), time=event.label()),
        Ref("observation_health", event.row.id),
    )


def _given_line(event: _Event) -> Readout:
    return _with_ref(
        READOUTS.render(FEVER_GIVEN_DOSE, time=event.label(), title=event.row.medication_given),
        Ref("observation_health", event.row.id),
    )


def _last_doses(
    courses: Sequence[MedicationCourse],
    doses: Sequence[MedicationDose],
    first_day: date,
    now: datetime,
    timezone: tzinfo,
) -> list[Readout]:
    """코스마다 체크된 가장 늦은 회차. 체크 안 된 회차는 보지 않는다 — 시각은 회차 예정 시각이다.

    아직 오지 않은 회차를 미리 눌렀으면 그 회차는 "마지막" 이 아니다 — 지금까지 온 회차만 본다.
    """
    out: list[Readout] = []
    for course in courses:
        if course.ends_on < first_day:
            continue  # 48시간과 겹치지 않는 코스
        checked = [
            d
            for d in doses
            if d.schedule_id == course.id
            and d.checked_at is not None
            and dose_moment(d, timezone) <= now
        ]
        if not checked:
            continue
        last = max(checked, key=lambda d: (d.dose_date, d.scheduled_time))
        out.append(
            _with_ref(
                READOUTS.render(
                    FEVER_LAST_DOSE,
                    title=course.title,
                    time=md_time(datetime.combine(last.dose_date, last.scheduled_time)),
                ),
                Ref("medication_schedule", course.id),
            )
        )
    return out


def _next_dose(
    courses: Sequence[MedicationCourse],
    doses: Sequence[MedicationDose],
    now: datetime,
    timezone: tzinfo,
) -> list[Readout]:
    """진행 중 코스의 회차 행 중 지금 이후 가장 이른 것. 행이 없으면 줄이 없다 — 계산하지 않는다.

    미리 체크한 회차는 "다음" 이 아니다 — 체크 안 된 회차만 본다.
    """
    active = {course.id: course for course in courses if course.status == "active"}
    upcoming = [
        (dose_moment(d, timezone), d.schedule_id)
        for d in doses
        if d.schedule_id in active and d.checked_at is None
    ]
    later = [item for item in upcoming if item[0] > now]
    if not later:
        return []
    moment, schedule_id = min(later, key=lambda item: item[0])
    return [
        _with_ref(
            READOUTS.render(FEVER_NEXT_DOSE, time=md_time(moment)),
            Ref("medication_schedule", schedule_id),
        )
    ]


def _with_ref(readout: Readout, ref: Ref) -> Readout:
    return Readout(
        kind=readout.kind,
        body=readout.body,
        authored_by=readout.authored_by,
        source_refs=(ref,),
    )
