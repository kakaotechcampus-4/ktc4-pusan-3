"""진료 준비(`visit_summary`)가 읽는 기록 — 증상 · 먹인 약 · 최근 식사 · 복약 체크.

🚨 모델은 줄을 고르지 않는다 (#551 리뷰). 모델이 정하는 것은 증상 · 먹인 약을 거슬러 볼 기간과 증상
   하나(`search_health_observations` 의 인자)뿐이고, 정리에 들어갈 줄은 코드가 다 넣는다.
   - 모델에게 고르게 했더니 식사 · 복약 조회를 건너뛰고(그 줄이 통째로 빠졌다) 증상 줄도 하나씩
     빠뜨렸다(라이브 eval T23 · T24). 날짜 · 집계로 정해지는 줄은 코드 몫이다(루트 CLAUDE.md §3)
   - 그래서 모델에게 돌려주는 것은 줄 수뿐이다 — 증상 원문 · 조치 · 약 이름 · 용량 · 식사 내용은
     외부 LLM 으로 가지 않는다(외부 LLM 전달 범위는 열린 결정이다 — 루트 CLAUDE.md §10)
   - 줄의 글은 코드가 그려(`visit_lines.py`) `state.visit_lines` 에 두고, 출력 tool 이 전부 묶는다

- 모델 tool 은 `search_health_observations` 하나다. 식사(`collect_meals`)와 복약 체크
  (`collect_medication_checks`)는 출력 tool 이 코드로 부른다
- 기간은 보호자 시간대의 **오늘 포함 N일**이다 (hook 의 7일 창과 같은 세는 법). 복약 체크는 모델이
  고른 기간 중 가장 긴 것(`state.visit_period`)을 따른다
- 증상은 에피소드 단위다. 사전 코드마다 묶고(`group_symptom_episodes`), 사전에 없는 말은 묶지 않고
  기록 하나가 한 줄이다 — 원문끼리 같다고 합치지 않는다
- 먹인 약 행(`medication_given`)은 에피소드에 들어가지 않고 따로 간다. 증상을 골랐으면 그 증상
  행에 묶인 약만 넣는다
- 체온 · 측정 칸은 싣지 않는다 — 열 타임라인(코드)이 같은 기록에서 직접 나열한다
- 복약 체크는 날짜 · 코스별 한 줄이다. 체크 안 된 회차는 "안 먹였다" 가 아니라 그날 회차 수에만
  들어간다. 아직 오지 않은 회차는 세지 않는다
- 같은 기록은 몇 번 읽어도 한 줄이다(`state.visit_keys`)
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from app.agents.common.refs import Ref
from app.agents.health.context import HealthContext, VisitLine, VisitSection
from app.agents.health.reference import symptom_codes
from app.agents.health.result import ErrorCode, ToolResult, fail, ok
from app.agents.health.schemas.records import Period, SearchHealthArgs
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
from app.rules.term_match import normalize

# 모델이 기간을 말하지 않았을 때 · 증상 조회를 부르지 않았을 때의 기간
DEFAULT_PERIOD: Period = "14d"
# 최근 식사는 3일만 본다
MEAL_PERIOD = "3d"

# 줄 key 머리글자 — state 안에서 줄을 가르는 이름이다. 모델에게는 가지 않는다
_PREFIX: dict[VisitSection, str] = {"symptom": "s", "given": "g", "medication": "c", "meal": "f"}


def first_day(today: date, period: str) -> date:
    """오늘을 포함해 N일 전의 첫날. `period` 는 "14d" 꼴이다."""
    return today - timedelta(days=_days(period) - 1)


async def search_health_observations(context: HealthContext, args: SearchHealthArgs) -> ToolResult:
    """모델 tool. 증상 에피소드 · 먹인 약 줄을 state 에 두고 줄 수만 돌려준다.

    둘 다 0건이면 `NO_RECORDS`.
    """
    symptoms, given = await collect_health(context, args.period, args.symptom)
    if not symptoms and not given:
        return fail(
            "query",
            "search_health_observations",
            ErrorCode.NO_RECORDS,
            "이 기간에 건강 기록이 없다. compose_symptom_timeline 을 부르면 식사 · 복약만 "
            "정리된다.",
        )
    return ok("query", "search_health_observations", symptom_lines=symptoms, given_lines=given)


async def collect_health(
    context: HealthContext, period: str, symptom: str | None = None
) -> tuple[int, int]:
    """증상 에피소드 · 먹인 약 줄을 state 에 둔다. (증상 줄 수, 먹인 약 줄 수).

    부른 기간 중 가장 긴 것을 `state.visit_period` 에 남긴다 — 복약 체크가 같은 기간을 본다.
    """
    state = context.state
    if state.visit_period is None or _days(period) > _days(state.visit_period):
        state.visit_period = period
    observations = await context.ports.memory.health_observations(
        child_id=context.child_id,
        date_from=first_day(context.today, period),
        date_to=context.today,
    )
    symptom_rows = [row for row in observations if row.symptoms]
    if symptom:
        symptom_rows = _only_symptom(symptom_rows, symptom)
    shown_ids = {row.id for row in symptom_rows}
    # 증상을 골랐으면 그 증상 행에 묶인 약만 — 다른 증상의 약을 끼워 넣지 않는다
    given_rows = [
        row
        for row in observations
        if row.medication_given and (not symptom or row.linked_symptom_id in shown_ids)
    ]
    episodes = _episodes(context, symptom_rows)
    for row in sorted(given_rows, key=lambda r: _given_sort(r, context)):
        _register(
            context,
            "given",
            row.id,
            VisitLine(
                section="given",
                day=given_day(row, context.timezone),
                body=given_line(row, context.timezone),
                refs=(Ref("observation_health", row.id),),
            ),
        )
    return episodes, len(given_rows)


async def collect_meals(context: HealthContext) -> int:
    """최근 3일 식사 줄을 state 에 둔다. 줄 수 — 0건이어도 진료 준비를 막지 않는다."""
    meals = await context.ports.memory.meals(
        child_id=context.child_id,
        date_from=first_day(context.today, MEAL_PERIOD),
        date_to=context.today,
    )
    for meal in sorted(meals, key=lambda m: m.observed_on):
        _register(
            context,
            "meal",
            meal.id,
            VisitLine(
                section="meal",
                day=meal.observed_on,
                body=meal_line(meal),
                refs=(Ref("observation_food", meal.id),),
            ),
        )
    return len(meals)


async def collect_medication_checks(context: HealthContext, period: str) -> int:
    """날짜 · 코스별 체크 줄을 state 에 둔다. 줄 수.

    끝났거나 멈춘 코스도 기간과 겹치면 넣는다. 코스는 있는데 아직 지난 회차가 없으면(오늘 시작해 첫
    회차 전) 줄이 없다 — 0/0 줄을 지어내지 않는다.
    """
    date_from = first_day(context.today, period)
    medication = context.ports.medication
    courses = await medication.courses_overlapping(
        child_id=context.child_id, date_from=date_from, date_to=context.today
    )
    if not courses:
        return 0
    rows = await medication.doses(
        schedule_ids=[course.id for course in courses], date_from=date_from, date_to=context.today
    )
    # 회차 예정 시각이 지금 이전인 것만 센다 — 아직 오지 않은 회차는 체크할 수 없었다
    due = [row for row in rows if dose_moment(row, context.timezone) <= context.now]
    stopped_at = {c.id: c.stopped_at for c in courses if c.status == "stopped" and c.stopped_at}

    found: list[tuple[date, str, object, VisitLine]] = []
    for course in courses:
        by_day: dict[date, list[MedicationDose]] = {}
        for row in due:
            if row.schedule_id == course.id and _planned(context, row, stopped_at.get(course.id)):
                by_day.setdefault(row.dose_date, []).append(row)
        for day, day_rows in by_day.items():
            checked = sum(1 for row in day_rows if row.checked_at is not None)
            line = VisitLine(
                section="medication",
                day=day,
                body=check_line(course, day, checked, len(day_rows)),
                refs=(Ref("medication_schedule", course.id),),
            )
            found.append((day, course.title, (course.id, day), line))
    found.sort(key=lambda item: (item[0], item[1]))
    for _, _, identity, line in found:
        _register(context, "medication", identity, line)
    return len(found)


def _days(period: str) -> int:
    return int(period.removesuffix("d"))


def _register(
    context: HealthContext, section: VisitSection, identity: object, line: VisitLine
) -> str:
    """줄을 state 에 둔다. 같은 기록이면 같은 key — 글은 새로 그린 것으로 둔다."""
    state = context.state
    key = state.visit_keys.get((section, identity))
    if key is None:
        count = sum(1 for (kind, _) in state.visit_keys if kind == section)
        key = f"{_PREFIX[section]}{count + 1}"
        state.visit_keys[(section, identity)] = key
    state.visit_lines[key] = line
    return key


def _episodes(context: HealthContext, rows: Sequence[HealthObservation]) -> int:
    """증상 에피소드 줄을 state 에 둔다. 줄 수."""
    tz = context.timezone
    by_id = {row.id: row for row in rows}
    found: list[tuple[date, date, VisitLine, tuple[str, ...]]] = []
    for episode in group_symptom_episodes(rows, timezone=tz):
        members = [by_id[record_id] for record_id in episode.observation_ids]
        line = symptom_line(
            episode.starts_on, episode.ends_on, code=episode.code, texts=(), members=members
        )
        found.append(_episode(episode.starts_on, episode.ends_on, line, members, (episode.code,)))
    # 사전에 없는 말은 기록 하나가 한 줄 — 묶지 않는다
    for row in rows:
        texts = [text for text in row.symptoms if not symptom_codes_of((text,))]
        if texts:
            days = observation_days(row, tz)
            line = symptom_line(days[0], days[-1], code=None, texts=texts, members=[row])
            found.append(_episode(days[0], days[-1], line, [row], ()))
    found.sort(key=lambda item: (item[0], item[1]))
    for _, _, line, codes in found:
        _register(context, "symptom", tuple(ref.id for ref in line.refs) + codes, line)
    return len(found)


def _episode(
    start: date,
    end: date,
    body: str,
    members: Sequence[HealthObservation],
    codes: tuple[str, ...],
) -> tuple[date, date, VisitLine, tuple[str, ...]]:
    refs = tuple(Ref("observation_health", row.id) for row in members)
    return start, end, VisitLine(section="symptom", day=start, body=body, refs=refs), codes


def _given_sort(row: HealthObservation, context: HealthContext) -> tuple[date, datetime]:
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
    """사전 코드가 같은 기록. 사전에 없는 말이면 그 말이 그대로 든 기록만 (원문끼리 같을 때만).

    요청 말이 사전 별칭에 안 걸려도 사전 이름 · 별칭의 조각이면 그 코드로 본다 — "요즘 열 얼마나
    났지" 의 "열" 은 별칭이 아니라서(오탐 때문에 "열나" · "열이" 만 둔다) 원문 비교로 빠지면 "38도"
    기록이 떨어져 에피소드가 잘렸다(라이브 eval T23). 여기는 저장이 아니라 보여 줄 범위라 넓게
    잡는다.
    """
    wanted = set(symptom_codes_of((symptom,))) or _codes_containing(symptom)
    if wanted:
        return [row for row in rows if wanted & set(symptom_codes_of(row.symptoms))]
    return [row for row in rows if any(symptom in text for text in row.symptoms)]


def _codes_containing(word: str) -> set[str]:
    """사전 이름이나 별칭에 이 말이 들어 있는 코드. "열" → fever(발열 · 열나 · 미열 …)."""
    needle = normalize(word)
    if not needle:
        return set()
    book = symptom_codes()
    return {
        term.key
        for term in book.terms
        if any(needle in normalize(name) for name in (book.labels[term.key], *term.aliases))
    }
