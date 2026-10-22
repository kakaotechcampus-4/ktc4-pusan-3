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
먼저 내는 것은 부르는 쪽(라벨)이다.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from uuid import UUID

from app.agents.common.readout import Readout
from app.agents.common.reference import age_gates
from app.agents.common.refs import Ref
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
from app.agents.health.store.ports import HealthObservation, MedicationCourse, MedicationDose
from app.agents.health.tools.symptoms import observation_days, symptom_codes_of

WINDOW = timedelta(hours=48)

# 발열로 보는 체온 하나. 부위별 기준값은 이슈 리뷰 결정 전이라 두지 않는다(#557).
# 출처 미확정 — 의료 검수 대상이다. 확정되면 출처를 이 주석과 명세에 적는다
FEVER_TEMPERATURE = Decimal("38.0")

# 이 월령 미만의 발열은 응급 안내가 먼저다. 값은 age_gates.yaml 이 정본이다 (의료 검수 대상)
INFANT_FEVER_BELOW_MONTH = age_gates().infant_fever_emergency_below_month

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
            return _md(self.day)
        return _md_time(self.moment)


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
            and _dose_moment(d, timezone) <= now
        ]
        if not checked:
            continue
        last = max(checked, key=lambda d: (d.dose_date, d.scheduled_time))
        out.append(
            _with_ref(
                READOUTS.render(
                    FEVER_LAST_DOSE,
                    title=course.title,
                    time=_md_time(datetime.combine(last.dose_date, last.scheduled_time)),
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
        (_dose_moment(d, timezone), d.schedule_id)
        for d in doses
        if d.schedule_id in active and d.checked_at is None
    ]
    later = [item for item in upcoming if item[0] > now]
    if not later:
        return []
    moment, schedule_id = min(later, key=lambda item: item[0])
    return [
        _with_ref(
            READOUTS.render(FEVER_NEXT_DOSE, time=_md_time(moment)),
            Ref("medication_schedule", schedule_id),
        )
    ]


def _dose_moment(dose: MedicationDose, timezone: tzinfo) -> datetime:
    """회차의 예정 시각 — 날짜 + 시각을 아이 타임존으로."""
    return datetime.combine(dose.dose_date, dose.scheduled_time, tzinfo=timezone)


def _with_ref(readout: Readout, ref: Ref) -> Readout:
    return Readout(
        kind=readout.kind,
        body=readout.body,
        authored_by=readout.authored_by,
        source_refs=(ref,),
    )


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _md_time(moment: datetime) -> str:
    """`10/14 23:00` — 날짜는 0 을 채우지 않는다(복약 현황 `dose.status` 와 같은 꼴)."""
    return f"{moment.month}/{moment.day} {moment:%H:%M}"
