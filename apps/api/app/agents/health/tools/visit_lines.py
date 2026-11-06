"""진료 준비 정리의 줄을 기록에서 그린다 — 모델 0회, 저장 없음.

모델은 줄을 고르기만 하고 글은 여기서 나온다 (#551). 그래서 모델에게 원문 · 약 이름 · 용량을 보낼
일이 없고, 모델이 쓴 문장을 사후에 거를 일도 없다.

- 증상 — 사전 코드는 에피소드 기간 + 사전 이름("9/20–9/22 발열"), 사전에 없는 말은 기록 원문.
  정도 · 조치를 붙인다. 원문(사전에 없는 증상 · 조치)은 `wording.shown` 으로 한 번 거른다
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
) -> str:
    """에피소드 한 줄. `code` 가 있으면 사전 이름, 없으면 기록 원문(거른 것)이다."""
    if code is not None:
        symptom = symptom_codes().labels[code]
    else:
        symptom = _SEPARATOR.join(dict.fromkeys(shown(text) for text in texts))
    severity = _joined(SEVERITY_LABEL.get(row.severity or "", row.severity) for row in members)
    action = _joined(shown(row.action_taken) for row in members if row.action_taken)
    detail = "".join(
        f" · {name}: {value}" for name, value in (("정도", severity), ("조치", action)) if value
    )
    return READOUTS.render(
        VISIT_SYMPTOM, days=span(start, end), symptom=symptom, detail=detail
    ).body


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


def _joined(values: Iterable[str | None]) -> str | None:
    found = list(dict.fromkeys(value for value in values if value))
    return _SEPARATOR.join(found) if found else None
