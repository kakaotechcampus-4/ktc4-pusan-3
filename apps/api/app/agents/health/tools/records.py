"""진료 준비(`visit_summary`)가 읽는 조회 tool — 증상 · 먹인 약 · 최근 식사 · 복약 체크.

모델은 이 줄들을 날짜순 문장으로 옮길 뿐이다. 줄마다 `ref` 가 있고, 돌려준 id 는 전부
`state.seen_refs` 에 적는다 — 출력 tool 이 줄의 ref 가 이번 run 이 조회한 것인지 이걸로 본다.

- 기간은 보호자 시간대의 **오늘 포함 N일**이다 (hook 의 7일 창과 같은 세는 법)
- 증상은 에피소드 단위다. 사전 코드마다 묶고(`group_symptom_episodes`), 사전에 없는 말은 묶지 않고
  기록 하나가 한 줄이다 — 원문끼리 같다고 합치지 않는다. 원문과 코드를 같이 싣는다
- 먹인 약 행(`medication_given`)은 에피소드에 들어가지 않고 시각순 줄로 따로 간다.
  `linked_symptom_ref` 는 연결된 증상 행이 **이번 결과에 있을 때만** 그 행의 id 다
- 체온 · 측정 칸은 싣지 않는다 — 열 타임라인(코드)이 같은 기록에서 직접 나열한다
- 식사 줄에는 영양 숫자가 없다
- 같은 id 로 `ref_texts`(보호자가 쓴 말)와 `ref_names`(약 줄의 이름 원문)도 적는다 — 출력 tool 의
  사후 검사가 줄을 원문과 대조한다. 원문은 로그에 싣지 않는다
- 증상 · 식사 · 먹인 약 id 는 `ref_days`(그 기록의 날짜 범위 — 에피소드 기간 · 먹은 날 · 먹인 날)도
  적는다. 정리 줄의 날짜가 그 범위 밖이면 사후 검사가 거절한다
- 복약 체크는 날짜 · 코스별 `checked` / `planned` 두 숫자뿐이다. 체크 안 된 회차는 "안 먹였다" 가
  아니라 `planned` 에만 들어간다. 오늘 이후 회차는 세지 않는다
"""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, tzinfo
from uuid import UUID

from app.agents.common.refs import Ref
from app.agents.health.context import HealthContext
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

_SEPARATOR = " / "


def _first_day(context: HealthContext, period: str) -> date:
    """오늘을 포함해 N일 전의 첫날. `period` 는 "14d" 꼴이다."""
    return context.today - timedelta(days=int(period.removesuffix("d")) - 1)


async def search_health_observations(context: HealthContext, args: SearchHealthArgs) -> ToolResult:
    """증상 에피소드 줄과 먹인 약 줄. 둘 다 0건이면 `NO_RECORDS`."""
    observations = await context.ports.memory.health_observations(
        child_id=context.child_id, date_from=_first_day(context, args.period), date_to=context.today
    )
    tz = context.timezone
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

    episodes = _episode_lines(symptom_rows, tz)
    given = sorted((_given_line(row, shown_ids, tz) for row in given_rows), key=_time_of)
    if not episodes and not given:
        return fail(
            "query",
            "search_health_observations",
            ErrorCode.NO_RECORDS,
            "이 기간에 건강 기록이 없다. 기록이 없다고 안내하고 내용을 지어내지 않는다.",
        )

    state = context.state
    seen = state.seen_refs
    for line in episodes:
        first, last = date.fromisoformat(line["from"]), date.fromisoformat(line["to"])
        for ref_id in line["refs"]:
            seen[ref_id] = Ref("observation_health", UUID(ref_id))
            _widen(state.ref_days, ref_id, first, last)
    for row in given_rows:
        seen[str(row.id)] = Ref("observation_health", row.id)
        _widen(state.ref_days, str(row.id), *_given_days(row, tz))
    for row in [*symptom_rows, *given_rows]:
        state.ref_texts[str(row.id)] = _raw_text(row)
        if row.medication_given:
            state.ref_names[str(row.id)] = row.medication_given
    return ok("query", "search_health_observations", episodes=episodes, given=given)


async def search_recent_meals(context: HealthContext, args: SearchMealsArgs) -> ToolResult:
    """최근 식사 줄. 0건이어도 실패가 아니다 — 곁들이는 줄이라 진료 준비를 막지 않는다."""
    meals = await context.ports.memory.meals(
        child_id=context.child_id, date_from=_first_day(context, args.period), date_to=context.today
    )
    lines = [
        {
            "ref": str(meal.id),
            "date": meal.observed_on.isoformat(),
            "subject": meal.subject,
            "amount_text": meal.amount_text,
        }
        for meal in sorted(meals, key=lambda m: m.observed_on)
    ]
    for meal in meals:
        context.state.seen_refs[str(meal.id)] = Ref("observation_food", meal.id)
        _widen(context.state.ref_days, str(meal.id), meal.observed_on, meal.observed_on)
        context.state.ref_texts[str(meal.id)] = " ".join(
            part for part in (meal.subject, meal.amount_text) if part
        )
    return ok("query", "search_recent_meals", meals=lines)


async def search_medication_checks(
    context: HealthContext, args: SearchMedicationChecksArgs
) -> ToolResult:
    """날짜 · 코스별 체크 수. 끝났거나 멈춘 코스도 기간과 겹치면 넣는다.

    겹치는 코스가 없으면 `NO_RECORDS`. 코스는 있는데 아직 지난 회차가 없으면(오늘 시작해 첫 회차
    전) 성공에 빈 목록이다 — 0/0 줄을 지어내지 않는다.
    """
    date_from = _first_day(context, args.period)
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
    due = [row for row in rows if _moment(context, row) <= context.now]
    stopped_at = {c.id: c.stopped_at for c in courses if c.status == "stopped" and c.stopped_at}

    lines: list[dict[str, object]] = []
    for course in courses:
        by_day: dict[date, list[MedicationDose]] = {}
        for row in due:
            if row.schedule_id == course.id and _planned(context, row, stopped_at.get(course.id)):
                by_day.setdefault(row.dose_date, []).append(row)
        for day, day_rows in by_day.items():
            checked = sum(1 for row in day_rows if row.checked_at is not None)
            lines.append(
                {
                    "ref": str(course.id),
                    "date": day.isoformat(),
                    "title": course.title,
                    "checked": checked,
                    "planned": len(day_rows),
                }
            )
            context.state.check_counts[(str(course.id), day)] = (checked, len(day_rows))
            state = context.state
            state.seen_refs[str(course.id)] = Ref("medication_schedule", course.id)
            # 등록한 원문 그대로 — 약 이름 · 용량을 줄과 글자 단위로 대조한다
            state.ref_names[str(course.id)] = course.title
            state.ref_texts[str(course.id)] = " ".join(
                part for part in (course.title, course.dosage) if part
            )
    lines.sort(key=lambda line: (str(line["date"]), str(line["title"])))
    return ok("query", "search_medication_checks", checks=lines)


def _raw_text(row: HealthObservation) -> str:
    """그 행에 보호자가 쓴 말 전부. 줄이 인용한 말이 원문에 있는지 볼 때만 쓴다."""
    parts = (*row.symptoms, row.severity, row.action_taken, row.medication_given)
    return " ".join(part for part in parts if part)


def _planned(context: HealthContext, row: MedicationDose, stopped_at: datetime | None) -> bool:
    """멈춘 코스의 멈춘 뒤 회차는 계획이 아니다 — 센 적도 체크된 적도 없으면 뺀다.

    멈춘 뒤에 체크가 찍힌 회차는 보호자가 한 일이라 사실이다. 지우지 않고 `planned` 에도 센다
    (`checked` 가 `planned` 보다 커지지 않게).
    """
    if stopped_at is None or row.checked_at is not None:
        return True
    return _moment(context, row) <= stopped_at


def _only_symptom(rows: list[HealthObservation], symptom: str) -> list[HealthObservation]:
    """사전 코드가 같은 기록. 사전에 없는 말이면 그 말이 그대로 든 기록만 (원문끼리 같을 때만)."""
    wanted = set(symptom_codes_of((symptom,)))
    if wanted:
        return [row for row in rows if wanted & set(symptom_codes_of(row.symptoms))]
    return [row for row in rows if any(symptom in text for text in row.symptoms)]


def _episode_lines(rows: Sequence[HealthObservation], tz: tzinfo) -> list[dict]:
    by_id = {row.id: row for row in rows}
    lines: list[dict] = []
    for episode in group_symptom_episodes(rows, timezone=tz):
        members = [by_id[record_id] for record_id in episode.observation_ids]
        texts = [
            text
            for row in members
            for text in row.symptoms
            if episode.code in symptom_codes_of((text,))
        ]
        lines.append(_line(episode.starts_on, episode.ends_on, texts, [episode.code], members, tz))
    # 사전에 없는 말은 기록 하나가 한 줄 — 묶지 않는다
    for row in rows:
        texts = [text for text in row.symptoms if not symptom_codes_of((text,))]
        if texts:
            days = observation_days(row, tz)
            lines.append(_line(days[0], days[-1], texts, [], [row], tz))
    lines.sort(key=lambda line: (line["from"], line["to"]))
    return lines


def _line(
    start: date,
    end: date,
    texts: Sequence[str],
    codes: list[str],
    members: Sequence[HealthObservation],
    tz: tzinfo,
) -> dict:
    ordered = sorted(members, key=lambda row: _when(row, tz))
    return {
        # ref 는 에피소드의 첫 기록. refs 는 묶은 기록 전부 (먹인 약의 연결은 이 중 하나를 가리킨다)
        "ref": str(ordered[0].id),
        "refs": [str(row.id) for row in ordered],
        "from": start.isoformat(),
        "to": end.isoformat(),
        "symptom": _SEPARATOR.join(dict.fromkeys(texts)),
        "codes": codes,
        # 보호자가 쓴 말 그대로. 기록마다 달랐으면 시간순으로 이어 둘 뿐 하나로 고르지 않는다
        "severity": _joined(row.severity for row in ordered),
        "action_taken": _joined(row.action_taken for row in ordered),
    }


def _joined(values: Iterable[str | None]) -> str | None:
    found = list(dict.fromkeys(value for value in values if value))
    return _SEPARATOR.join(found) if found else None


def _given_line(row: HealthObservation, shown_ids: set[UUID], tz: tzinfo) -> dict:
    return {
        "ref": str(row.id),
        "time": _when(row, tz),
        "medication_given": row.medication_given,
        # 연결된 증상 행이 이번 결과에 있을 때만 — 보지 못한 행을 가리키지 않는다
        "linked_symptom_ref": (
            str(row.linked_symptom_id) if row.linked_symptom_id in shown_ids else None
        ),
    }


def _when(row: HealthObservation, tz: tzinfo) -> str:
    """몇 시인지 말했으면 아이 타임존의 분 단위 시각, 아니면 날짜만 (시각을 짐작하지 않는다)."""
    if row.observed_time is None:
        return row.observed_from.isoformat()
    return row.observed_time.astimezone(tz).strftime("%Y-%m-%dT%H:%M")


def _time_of(line: dict) -> str:
    return line["time"]


def _given_days(row: HealthObservation, tz: tzinfo) -> tuple[date, date]:
    """먹인 약 행의 날짜 — 말한 시각이 있으면 아이 타임존의 그날, 없으면 기록한 기간."""
    if row.observed_time is None:
        return row.observed_from, row.observed_to
    day = row.observed_time.astimezone(tz).date()
    return day, day


def _widen(days: dict[str, tuple[date, date]], key: str, first: date, last: date) -> None:
    """한 기록이 여러 에피소드(코드)에 들면 그 범위를 합친다."""
    if key in days:
        first, last = min(first, days[key][0]), max(last, days[key][1])
    days[key] = (first, last)


def _moment(context: HealthContext, row: MedicationDose) -> datetime:
    """회차의 예정 시각 — 날짜(KST) + 시각을 아이 타임존으로 (`doses.py` 와 같은 기준)."""
    return datetime.combine(row.dose_date, row.scheduled_time, tzinfo=context.timezone)
