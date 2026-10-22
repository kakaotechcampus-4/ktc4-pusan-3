"""진료 준비(`visit_summary`)가 읽는 조회 tool — 증상 · 먹인 약 · 최근 식사 · 복약 체크.

🚨 모델에게는 줄의 key · 날짜 · 증상 코드만 돌려준다 (#551). 증상 원문 · 조치 · 약 이름 · 용량 ·
   식사 내용은 외부 LLM 으로 보내지 않는다 — 줄의 글은 코드가 그려(`visit_lines.py`)
   `state.visit_lines` 에 두고, 출력 tool 이 모델이 고른 key 의 줄만 낸다. 외부 LLM 전달 범위가
   아직 열린 결정이라(루트 CLAUDE.md §10) 정리에 꼭 필요한 것만 보낸다.

- 기간은 보호자 시간대의 **오늘 포함 N일**이다 (hook 의 7일 창과 같은 세는 법)
- 증상은 에피소드 단위다. 사전 코드마다 묶고(`group_symptom_episodes`), 사전에 없는 말은 묶지 않고
  기록 하나가 한 줄이다(코드는 빈 목록) — 원문끼리 같다고 합치지 않는다
- 먹인 약 행(`medication_given`)은 에피소드에 들어가지 않고 따로 간다. `symptom_key` 는 연결된
  증상 행이 **이번 결과의 줄에 있을 때만** 그 줄의 key 다
- 체온 · 측정 칸은 싣지 않는다 — 열 타임라인(코드)이 같은 기록에서 직접 나열한다
- 복약 체크는 날짜 · 코스별 한 줄이다. 체크 안 된 회차는 "안 먹였다" 가 아니라 그날 회차 수에만
  들어간다. 아직 오지 않은 회차는 세지 않는다
- 같은 조회를 다시 불러도 같은 기록은 같은 key 다(`state.visit_keys`)
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.agents.common.refs import Ref
from app.agents.health.context import HealthContext, VisitLine, VisitSection
from app.agents.health.result import ErrorCode, ToolResult, fail, ok
from app.agents.health.schemas.records import (
    SearchHealthArgs,
    SearchMealsArgs,
    SearchMedicationChecksArgs,
)
from app.agents.health.store.ports import HealthObservation, MedicationDose
from app.agents.health.tools.symptoms import (
    group_symptom_episodes,
    observation_days,
    symptom_codes_of,
)
from app.agents.health.tools.visit_lines import (
    check_line,
    given_day,
    given_line,
    meal_line,
    symptom_line,
)
from app.agents.health.when import dose_moment

# key 머리글자 — 모델이 key 만 보고도 무슨 줄인지 안다
_PREFIX: dict[VisitSection, str] = {"symptom": "s", "given": "g", "medication": "c", "meal": "f"}


def first_day(today: date, period: str) -> date:
    """오늘을 포함해 N일 전의 첫날. `period` 는 "14d" 꼴이다."""
    return today - timedelta(days=int(period.removesuffix("d")) - 1)


async def search_health_observations(context: HealthContext, args: SearchHealthArgs) -> ToolResult:
    """증상 에피소드 줄과 먹인 약 줄. 둘 다 0건이면 `NO_RECORDS`."""
    observations = await context.ports.memory.health_observations(
        child_id=context.child_id,
        date_from=first_day(context.today, args.period),
        date_to=context.today,
    )
    symptom_rows = [row for row in observations if row.symptoms]
    if args.symptom:
        symptom_rows = _only_symptom(symptom_rows, args.symptom)
    shown_ids = {row.id for row in symptom_rows}
    # 증상을 골랐으면 그 증상 행에 묶인 약만 — 다른 증상의 약을 끼워 넣지 않는다
    given_rows = [
        row
        for row in observations
        if row.medication_given and (not args.symptom or row.linked_symptom_id in shown_ids)
    ]
    if not symptom_rows and not given_rows:
        return fail(
            "query",
            "search_health_observations",
            ErrorCode.NO_RECORDS,
            "이 기간에 건강 기록이 없다. 기록이 없다고 안내하고 내용을 지어내지 않는다.",
        )

    episodes, key_of_row = _episodes(context, symptom_rows)
    given = [
        {
            "key": _register(
                context,
                "given",
                row.id,
                VisitLine(
                    section="given",
                    day=given_day(row, context.timezone),
                    body=given_line(row, context.timezone),
                    refs=(Ref("observation_health", row.id),),
                ),
            ),
            "date": given_day(row, context.timezone).isoformat(),
            # 연결된 증상이 이번 결과의 줄에 있을 때만 — 보지 못한 줄을 가리키지 않는다
            "symptom_key": key_of_row.get(row.linked_symptom_id) if row.linked_symptom_id else None,
        }
        for row in sorted(given_rows, key=lambda r: _given_sort(r, context))
    ]
    return ok("query", "search_health_observations", symptoms=episodes, given=given)


async def search_recent_meals(context: HealthContext, args: SearchMealsArgs) -> ToolResult:
    """최근 식사 줄. 0건이어도 실패가 아니다 — 곁들이는 줄이라 진료 준비를 막지 않는다."""
    meals = await context.ports.memory.meals(
        child_id=context.child_id,
        date_from=first_day(context.today, args.period),
        date_to=context.today,
    )
    lines = [
        {
            "key": _register(
                context,
                "meal",
                meal.id,
                VisitLine(
                    section="meal",
                    day=meal.observed_on,
                    body=meal_line(meal),
                    refs=(Ref("observation_food", meal.id),),
                ),
            ),
            "date": meal.observed_on.isoformat(),
        }
        for meal in sorted(meals, key=lambda m: m.observed_on)
    ]
    return ok("query", "search_recent_meals", meals=lines)


async def search_medication_checks(
    context: HealthContext, args: SearchMedicationChecksArgs
) -> ToolResult:
    """날짜 · 코스별 체크 줄. 끝났거나 멈춘 코스도 기간과 겹치면 넣는다.

    겹치는 코스가 없으면 `NO_RECORDS`. 코스는 있는데 아직 지난 회차가 없으면(오늘 시작해 첫 회차
    전) 성공에 빈 목록이다 — 0/0 줄을 지어내지 않는다.
    """
    date_from = first_day(context.today, args.period)
    medication = context.ports.medication
    courses = await medication.courses_overlapping(
        child_id=context.child_id, date_from=date_from, date_to=context.today
    )
    if not courses:
        return fail(
            "query",
            "search_medication_checks",
            ErrorCode.NO_RECORDS,
            "이 기간에 등록된 복약이 없다. 없다고 안내하고 내용을 지어내지 않는다.",
        )
    rows = await medication.doses(
        schedule_ids=[course.id for course in courses], date_from=date_from, date_to=context.today
    )
    # 회차 예정 시각이 지금 이전인 것만 센다 — 아직 오지 않은 회차는 체크할 수 없었다
    due = [row for row in rows if dose_moment(row, context.timezone) <= context.now]
    stopped_at = {c.id: c.stopped_at for c in courses if c.status == "stopped" and c.stopped_at}

    found: list[tuple[date, str, dict[str, Any]]] = []
    for course in courses:
        by_day: dict[date, list[MedicationDose]] = {}
        for row in due:
            if row.schedule_id == course.id and _planned(context, row, stopped_at.get(course.id)):
                by_day.setdefault(row.dose_date, []).append(row)
        for day, day_rows in by_day.items():
            checked = sum(1 for row in day_rows if row.checked_at is not None)
            key = _register(
                context,
                "medication",
                (course.id, day),
                VisitLine(
                    section="medication",
                    day=day,
                    body=check_line(course, day, checked, len(day_rows)),
                    refs=(Ref("medication_schedule", course.id),),
                ),
            )
            found.append((day, course.title, {"key": key, "date": day.isoformat()}))
    found.sort(key=lambda item: (item[0], item[1]))
    return ok("query", "search_medication_checks", checks=[line for *_, line in found])


def _register(
    context: HealthContext, section: VisitSection, identity: object, line: VisitLine
) -> str:
    """줄을 state 에 두고 key 를 돌려준다. 같은 기록이면 같은 key — 글은 새로 그린 것으로 둔다."""
    state = context.state
    key = state.visit_keys.get((section, identity))
    if key is None:
        count = sum(1 for (kind, _) in state.visit_keys if kind == section)
        key = f"{_PREFIX[section]}{count + 1}"
        state.visit_keys[(section, identity)] = key
    state.visit_lines[key] = line
    return key


def _episodes(
    context: HealthContext, rows: Sequence[HealthObservation]
) -> tuple[list[dict[str, Any]], dict[UUID, str]]:
    """(모델에게 줄 에피소드 줄, 증상 행 id → 그 행이 든 줄의 key)."""
    tz = context.timezone
    by_id = {row.id: row for row in rows}
    found: list[tuple[date, date, VisitLine, list[str]]] = []
    for episode in group_symptom_episodes(rows, timezone=tz):
        members = [by_id[record_id] for record_id in episode.observation_ids]
        line = symptom_line(
            episode.starts_on, episode.ends_on, code=episode.code, texts=(), members=members
        )
        found.append(_episode(episode.starts_on, episode.ends_on, line, members, [episode.code]))
    # 사전에 없는 말은 기록 하나가 한 줄 — 묶지 않는다
    for row in rows:
        texts = [text for text in row.symptoms if not symptom_codes_of((text,))]
        if texts:
            days = observation_days(row, tz)
            line = symptom_line(days[0], days[-1], code=None, texts=texts, members=[row])
            found.append(_episode(days[0], days[-1], line, [row], []))
    found.sort(key=lambda item: (item[0], item[1]))

    lines: list[dict[str, Any]] = []
    key_of_row: dict[UUID, str] = {}
    for start, end, line, codes in found:
        identity = tuple(ref.id for ref in line.refs) + tuple(codes)
        key = _register(context, "symptom", identity, line)
        for ref in line.refs:
            key_of_row.setdefault(ref.id, key)
        lines.append({"key": key, "from": start.isoformat(), "to": end.isoformat(), "codes": codes})
    return lines, key_of_row


def _episode(
    start: date, end: date, body: str, members: Sequence[HealthObservation], codes: list[str]
) -> tuple[date, date, VisitLine, list[str]]:
    refs = tuple(Ref("observation_health", row.id) for row in members)
    return start, end, VisitLine(section="symptom", day=start, body=body, refs=refs), codes


def _given_sort(row: HealthObservation, context: HealthContext) -> tuple[date, datetime | None]:
    """먹인 날 → 말한 시각. 시각을 말하지 않은 기록은 그날 끝이다."""
    moment = row.observed_time.astimezone(context.timezone) if row.observed_time else None
    return given_day(row, context.timezone), moment or datetime.max.replace(tzinfo=context.timezone)


def _planned(context: HealthContext, row: MedicationDose, stopped_at: datetime | None) -> bool:
    """멈춘 코스의 멈춘 뒤 회차는 계획이 아니다 — 센 적도 체크된 적도 없으면 뺀다.

    멈춘 뒤에 체크가 찍힌 회차는 보호자가 한 일이라 사실이다. 지우지 않고 그날 회차 수에도 센다
    (체크 수가 회차 수보다 커지지 않게).
    """
    if stopped_at is None or row.checked_at is not None:
        return True
    return dose_moment(row, context.timezone) <= stopped_at


def _only_symptom(rows: list[HealthObservation], symptom: str) -> list[HealthObservation]:
    """사전 코드가 같은 기록. 사전에 없는 말이면 그 말이 그대로 든 기록만 (원문끼리 같을 때만)."""
    wanted = set(symptom_codes_of((symptom,)))
    if wanted:
        return [row for row in rows if wanted & set(symptom_codes_of(row.symptoms))]
    return [row for row in rows if any(symptom in text for text in row.symptoms)]
