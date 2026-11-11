"""진료 준비 정리의 줄을 기록에서 그린다 — 모델 0회, 저장 없음.

모델은 줄을 고르기만 하고 글은 여기서 나온다 (#551). 그래서 모델에게 원문 · 약 이름 · 용량을 보낼
일이 없고, 모델이 쓴 문장을 사후에 거를 일도 없다.

- 증상 — 사전 코드는 에피소드 기간 + 사전 이름("9/20–9/22 발열"), 사전에 없는 말은 기록 원문.
  체온 · 정도 · 조치를 붙인다. 원문(사전에 없는 증상 · 조치)은 `wording.shown` 으로 한 번 거른다.
  체온은 보호자가 적은 숫자 그대로 — 하나면 그 값, 여럿이면 최고 · 최저와 잰 시각. 진료에서 가장
  먼저 묻는 값이라 정리에서 빠지면 안 된다("병원 가려는데 최근 증상 정리해줘" 가 "발열" 만 냈다)
- 먹인 약 — 보호자가 말한 약 이름 그대로. Memory 가 저장할 때 발화와 글자 단위로 대조했다
  (`memory/tools/observation.py` 의 `_said`) — 그래서 거르지 않는다(열 타임라인과 같다)
- 복약 체크 — 등록한 약 이름 · 용량 원문과 그날 체크 수 n/m. 체크 안 된 회차는 m 에만 든다
- 식사 — 날짜 · 먹은 것 · 보호자가 쓴 양 원문. `wording.shown` 으로 거른다
"""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, tzinfo

from app.agents.health.readouts import (
    READOUTS,
    VISIT_GIVEN,
    VISIT_MEAL,
    VISIT_MED_CHECK,
    VISIT_SYMPTOM,
)
from app.agents.health.reference import symptom_codes
from app.agents.health.store.ports import HealthObservation, MealRecord, MedicationCourse
from app.agents.health.tools.fever import SITE_LABEL
from app.agents.health.tools.symptoms import observation_days
from app.agents.health.when import md, md_time, span
from app.agents.health.wording import shown

_SEPARATOR = " / "
# 보호자 기록의 정도(`observation_health.severity`) — 판정이 아니라 보호자가 말한 정도다
SEVERITY_LABEL = {
    "mild": "약함",
    "moderate": "중간",
    "severe": "심함",
    "emergency": "매우 심함",
}


def symptom_line(
    start: date,
    end: date,
    *,
    code: str | None,
    texts: Sequence[str],
    members: Sequence[HealthObservation],
    timezone: tzinfo,
) -> str:
    """에피소드 한 줄. `code` 가 있으면 사전 이름, 없으면 기록 원문(거른 것)이다."""
    if code is not None:
        symptom = symptom_codes().labels[code]
    else:
        symptom = _SEPARATOR.join(dict.fromkeys(shown(text) for text in texts))
    temperature = temperature_detail(members, timezone)
    severity = _joined(SEVERITY_LABEL.get(row.severity or "", row.severity) for row in members)
    action = _joined(shown(row.action_taken) for row in members if row.action_taken)
    detail = "".join(
        f" · {name}: {value}"
        for name, value in (("체온", temperature), ("정도", severity), ("조치", action))
        if value
    )
    return READOUTS.render(
        VISIT_SYMPTOM, days=span(start, end), symptom=symptom, detail=detail
    ).body


def temperature_detail(members: Sequence[HealthObservation], timezone: tzinfo) -> str | None:
    """에피소드 기록에 적힌 체온 — 하나면 그 값, 여럿이면 최고 · 최저. 없으면 None.

    보호자가 적은 숫자를 그대로 쓰고 해석하지 않는다(열 타임라인의 `fever.peak` · `fever.low` 와
    같은 고르는 법). 시각은 `measured_at` → `observed_time` 순이고, 둘 다 없으면 날짜만 쓴다 —
    시각을 짐작하지 않는다. 같은 값이면 먼저 잰 것이다.
    """
    measured = sorted(
        (row for row in members if row.temperature is not None),
        key=lambda row: _measure_key(row, timezone),
    )
    if not measured:
        return None
    if len(measured) == 1:
        return _reading(measured[0], timezone)
    peak = max(measured, key=lambda row: row.temperature or 0)
    low = min(measured, key=lambda row: row.temperature or 0)
    return f"최고 {_reading(peak, timezone)}{_SEPARATOR}최저 {_reading(low, timezone)}"


def given_line(row: HealthObservation, tz: tzinfo) -> str:
    """먹인 약 한 줄. 몇 시인지 말했으면 시각까지, 아니면 날짜만(시각을 짐작하지 않는다)."""
    assert row.medication_given is not None
    time = md(row.observed_from) if row.observed_time is None else md_time(_local(row, tz))
    return READOUTS.render(VISIT_GIVEN, time=time, title=row.medication_given).body


def check_line(course: MedicationCourse, day: date, checked: int, planned: int) -> str:
    title = " ".join(part for part in (course.title, course.dosage) if part)
    return READOUTS.render(
        VISIT_MED_CHECK, day=md(day), title=title, checked=checked, planned=planned
    ).body


def meal_line(meal: MealRecord) -> str:
    text = " ".join(part for part in (meal.subject, meal.amount_text) if part)
    return READOUTS.render(VISIT_MEAL, day=md(meal.observed_on), text=shown(text)).body


def given_day(row: HealthObservation, tz: tzinfo) -> date:
    """먹인 약 행의 날 — 말한 시각이 있으면 아이 타임존의 그날, 없으면 기록한 첫날."""
    return row.observed_from if row.observed_time is None else _local(row, tz).date()


def _local(row: HealthObservation, tz: tzinfo) -> datetime:
    assert row.observed_time is not None
    return row.observed_time.astimezone(tz)


def _measured_moment(row: HealthObservation, tz: tzinfo) -> datetime | None:
    moment = row.measured_at or row.observed_time
    return moment.astimezone(tz) if moment is not None else None


def _measure_key(row: HealthObservation, tz: tzinfo) -> tuple[date, int, datetime | None]:
    """시간 흐름 순 — 시각을 모르는 기록은 그날 끝이다(열 타임라인과 같다)."""
    moment = _measured_moment(row, tz)
    if moment is None:
        return (observation_days(row, tz)[0], 1, None)
    return (moment.date(), 0, moment)


def _reading(row: HealthObservation, tz: tzinfo) -> str:
    """`38.5℃ (9/28 01:00, 귀)` — 시각을 모르면 날짜만, 부위를 모르면 부위 없이."""
    moment = _measured_moment(row, tz)
    when = md_time(moment) if moment is not None else md(observation_days(row, tz)[0])
    site = SITE_LABEL.get(row.measure_site or "")
    return f"{row.temperature}℃ ({when}, {site})" if site else f"{row.temperature}℃ ({when})"


def _joined(values: Iterable[str | None]) -> str | None:
    found = list(dict.fromkeys(value for value in values if value))
    return _SEPARATOR.join(found) if found else None
