"""검진 맥락 요약 `summarize_checkup_context` — 모델 0회, 저장 없음.

검진에 들고 갈 기록만 항목별로 나열한다. `schedule_check` 의 `context` 분기가 부른다.

- 최근 3일 식사 · 진행 중 복약 · 최근 30일 증상 에피소드 · 알레르기 · 만성질환. 보호자가 남기거나
  등록한 말 그대로이고 판정 · 비교 · 해석 · 영양 숫자가 없다
- 기록이 없는 항목은 줄을 내지 않는다 — 기록 없음이 "없었다" 는 아니다. 복약 · 안전 줄은 등록
  기반이라 비었으면 "등록된 내용이 없어요" 다(`card.empty` 와 같은 문구)
- 보호자 기록의 자유 문장(식사 · 사전에 없는 증상)은 그대로 낼 때 코드가 한 번 거른다. 해석 표현 ·
  병 이름처럼 읽히는 말(`review.py` 의 표)이나 측정 · 성장 평가 표현이 들었으면 원문을 내지 않고
  `FLAGGED` 자리표시로 바꾼다 — 조용히 빼지도 않는다. 이 요약은 기록 원문이 곧 내보내는 글이라
  "보호자가 그 말을 했다" 는 인용 예외가 없다 (Memory 가 뽑은 문장이라 원문이라고 보장도 못 한다)
- 발달 문항 · 문진 답 · 수면 시간 · 키 · 몸무게는 **읽지 않는다.** 아이 신체 정보 포트도
  부르지 않는다
- 안전 정보를 못 읽었으면 `safety.unavailable` 경고를 앞에 두고 안전 줄은 만들지 않는다 —
  읽지 못한 것을 "등록된 내용이 없어요" 로 쓰지 않는다 (진료 준비 `tools/timeline.py` 와 같은 약속)
- 끝에 늘 `checkup.context_footer`. 문진표 대필 요청이면 `checkup.no_ghostwrite` 가 맨 앞이다 —
  요약은 그대로 낸다. 문진표는 보호자가 쓰는 것이다
"""

import re
from collections.abc import Iterable, Sequence
from datetime import date, timedelta, tzinfo
from uuid import UUID

from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.health.context import HealthContext
from app.agents.health.readouts import (
    CARD_EMPTY,
    CHECKUP_CONTEXT_FOOTER,
    CHECKUP_CTX_ILLNESS,
    CHECKUP_CTX_MEALS,
    CHECKUP_CTX_MEDS,
    CHECKUP_CTX_SAFETY,
    CHECKUP_NO_GHOSTWRITE,
    READOUTS,
    SAFETY_UNAVAILABLE,
)
from app.agents.health.reference import symptom_codes
from app.agents.health.review import (
    FORBIDDEN_DISEASE_NAMES,
    FORBIDDEN_INTERPRETATION,
    FORBIDDEN_SUFFIX,
    _squash,
)
from app.agents.health.store.ports import HealthObservation, MealRecord, MedicationCourse
from app.agents.health.tools.symptoms import (
    group_symptom_episodes,
    observation_days,
    symptom_codes_of,
)

# 오늘을 포함한 N일 — 조회 tool 의 기간과 같은 세는 법이다
MEAL_DAYS = 3
ILLNESS_DAYS = 30

# 이 요약에 나가면 안 되는 측정 · 성장 평가 말 — 발달 평가로 읽힌다(루트 CLAUDE.md §2).
# "키" 는 낱말일 때만 본다: 뒤에 조사만 붙을 수 있어서 "키위" · "키즈" 는 걸리지 않는다
MEASUREMENT_WORDS: tuple[str, ...] = ("cm", "kg", "백분위", "또래", "성장곡선", "몸무게", "체중")
_HEIGHT = re.compile(r"(?<![가-힣])키(?:가|는|를|도|만|로|에|와|과|은|을)?(?![가-힣])")
# 걸린 기록 한 건 자리에 낼 중립 문구 — 사실만 말한다 (가안, PR 리뷰 포인트)
FLAGGED = "기록 1건 (표현은 기록에서 확인)"

_ITEM_SEPARATOR = ", "
_DAY_SEPARATOR = " / "
_RANGE_DASH = "–"  # 9/18–9/20


async def summarize_checkup_context(
    context: HealthContext, *, ghostwrite: bool
) -> tuple[Readout, ...]:
    """들고 갈 기록 줄들. `ghostwrite` 면 거절 문구가 맨 앞이다."""
    readouts: list[Readout] = []
    if ghostwrite:
        readouts.append(READOUTS.render(CHECKUP_NO_GHOSTWRITE))
    unavailable = _safety_unavailable(context)
    if unavailable:
        # "아래 요약은 그것 없이 만든 거예요" — 그래서 맨 위(대필 거절 다음)다
        readouts.append(READOUTS.render(SAFETY_UNAVAILABLE))

    readouts.extend(await _meals(context))
    readouts.append(await _medications(context))
    readouts.extend(await _illness(context))
    if not unavailable:
        readouts.append(_safety(context))
    readouts.append(READOUTS.render(CHECKUP_CONTEXT_FOOTER))
    return tuple(readouts)


def _safety_unavailable(context: HealthContext) -> bool:
    """안전 정보를 못 읽었다. 게이트가 비어 있으면 읽었는지 모르는 것이라 같이 본다."""
    gate = context.state.gate
    return gate is None or not gate.safety_ok


def _with_refs(readout: Readout, refs: Iterable[Ref]) -> Readout:
    return Readout(
        kind=readout.kind,
        body=readout.body,
        authored_by=readout.authored_by,
        source_refs=tuple(dict.fromkeys(refs)),
    )


def _unsafe(text: str) -> bool:
    """해석 · 병 이름 · 측정 · 성장 평가 표현이 있다. 원문을 내보내지 않는다."""
    squashed = _squash(text)
    return (
        any(word in squashed for word in FORBIDDEN_INTERPRETATION)
        or FORBIDDEN_SUFFIX.search(text) is not None
        or any(name in squashed for name in FORBIDDEN_DISEASE_NAMES)
        or any(word in squashed.lower() for word in MEASUREMENT_WORDS)
        or _HEIGHT.search(text) is not None
    )


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _span(start: date, end: date) -> str:
    return _md(start) if start == end else f"{_md(start)}{_RANGE_DASH}{_md(end)}"


async def _meals(context: HealthContext) -> list[Readout]:
    """최근 3일 식사 — 날짜 · 먹은 것 · 보호자가 쓴 양 원문. 하루 안에서는 기록한 순서다."""
    meals = await context.ports.memory.meals(
        child_id=context.child_id,
        date_from=context.today - timedelta(days=MEAL_DAYS - 1),
        date_to=context.today,
    )
    if not meals:
        return []
    by_day: dict[date, list[MealRecord]] = {}
    for meal in sorted(meals, key=lambda m: m.observed_on):  # 안정 정렬 — 같은 날은 들어온 순서
        by_day.setdefault(meal.observed_on, []).append(meal)
    text = _DAY_SEPARATOR.join(
        f"{_md(day)} " + _ITEM_SEPARATOR.join(_meal_text(m) for m in rows)
        for day, rows in by_day.items()
    )
    return [
        _with_refs(
            READOUTS.render(CHECKUP_CTX_MEALS, text=text),
            (Ref("observation_food", meal.id) for meal in meals),
        )
    ]


def _meal_text(meal: MealRecord) -> str:
    text = " ".join(part for part in (meal.subject, meal.amount_text) if part)
    return FLAGGED if _unsafe(text) else text


async def _medications(context: HealthContext) -> Readout:
    """진행 중 복약 — 등록한 이름과 용량 원문. 없으면 "등록된 내용이 없어요"(등록 기반이라 사실)."""
    courses = await context.ports.medication.active_courses(
        child_id=context.child_id, today=context.today
    )
    if not courses:
        return READOUTS.render(CHECKUP_CTX_MEDS, items=READOUTS.render(CARD_EMPTY).body)
    return _with_refs(
        READOUTS.render(
            CHECKUP_CTX_MEDS, items=_ITEM_SEPARATOR.join(_course_text(c) for c in courses)
        ),
        (Ref("medication_schedule", course.id) for course in courses),
    )


def _course_text(course: MedicationCourse) -> str:
    return " ".join(part for part in (course.title, course.dosage) if part)


async def _illness(context: HealthContext) -> list[Readout]:
    """최근 30일 증상 에피소드 — "9/18–9/20 발열". 먹인 약 행은 증상이 아니라 넣지 않는다."""
    observations = await context.ports.memory.health_observations(
        child_id=context.child_id,
        date_from=context.today - timedelta(days=ILLNESS_DAYS - 1),
        date_to=context.today,
    )
    rows = [row for row in observations if row.symptoms]
    items = _items(rows, context.timezone)
    if not items:
        return []
    # 같은 날 시작이면 쓴 순서. 시작일 → 끝일 순
    items.sort(key=lambda item: (item[0], item[1]))
    return [
        _with_refs(
            READOUTS.render(CHECKUP_CTX_ILLNESS, items=_ITEM_SEPARATOR.join(i[2] for i in items)),
            (Ref("observation_health", record_id) for item in items for record_id in item[3]),
        )
    ]


def _items(
    rows: Sequence[HealthObservation], timezone: tzinfo
) -> list[tuple[date, date, str, tuple[UUID, ...]]]:
    """(시작일, 끝일, 표시, 기록 id). 사전 코드는 에피소드로, 없는 말은 기록 한 건이 한 줄."""
    labels = symptom_codes().labels
    items: list[tuple[date, date, str, tuple[UUID, ...]]] = []
    for episode in group_symptom_episodes(rows, timezone=timezone):
        text = f"{_span(episode.starts_on, episode.ends_on)} {labels[episode.code]}"
        items.append((episode.starts_on, episode.ends_on, text, episode.observation_ids))
    for row in rows:
        unknown = [text for text in row.symptoms if not symptom_codes_of((text,))]
        if unknown:
            days = observation_days(row, timezone)
            shown = [text for text in dict.fromkeys(unknown) if not _unsafe(text)]
            if len(shown) < len(set(unknown)):
                shown.append(FLAGGED)
            text = f"{_span(days[0], days[-1])} {' / '.join(shown)}"
            items.append((days[0], days[-1], text, (row.id,)))
    return items


def _safety(context: HealthContext) -> Readout:
    """등록된(active) 알레르기 · 만성질환 이름 원문. 하나도 없으면 "등록된 내용이 없어요"(사실)."""
    active = [entry for entry in context.state.safety_entries if entry.status == "active"]
    ordered = [e.label for e in active if e.kind == "allergy"] + [
        e.label for e in active if e.kind == "chronic_disease"
    ]
    items = _ITEM_SEPARATOR.join(ordered) if ordered else READOUTS.render(CARD_EMPTY).body
    return READOUTS.render(CHECKUP_CTX_SAFETY, items=items)
