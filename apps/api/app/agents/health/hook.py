"""코드 hook — Memory 가 `observation_health` 를 저장한 직후 pipeline 이 부른다 (연결은 다른 이슈).

`check_symptom_repetition` 은 라벨이 아니다. 모델이 "이 기록은 반복이다" 라고 붙이는 게 아니라,
코드가 최근 7일의 증상 기록을 읽어 **기록된 날 수**를 센다. 센 값은 저장하지 않는다 — 부를 때마다
기록에서 다시 세므로 정정(wrong)으로 내린 기록은 다음 호출부터 저절로 빠지고, 보호자가 말한
"세 번째" 는 믿지 않고 기록으로 센다. 쓰기 0건 · 승인 0건 · 모델 0회.

단위는 날이다(아이 타임존 달력 날짜) — 같은 날 여러 번은 1일, 사흘 내리면 3일, 기간 기록은 그 날들을
다 센다. 에피소드로 세지 않는다. 저장이 검색보다 먼저여야 해서(루트 §4) 방금 저장한 기록도 읽힌다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo

from app.agents.common.readout import Readout
from app.agents.health.context import HealthContext
from app.agents.health.readouts import READOUTS, SYMPTOM_REPEAT, SYMPTOM_REPEAT_CONDITION
from app.agents.health.reference import Guidance, condition_codes, condition_guidance, symptom_codes
from app.agents.health.store.ports import HealthObservation, HealthSafetyEntry, SafetyLookupError
from app.agents.health.tools.symptoms import observation_days, symptom_codes_of
from app.rules.term_match import match_terms

REPEAT_WINDOW_DAYS = 7  # 오늘 포함
REPEAT_MIN_DAYS = 3
# 질환 문장을 붙이는 health_safety 항목. 알레르기는 질환이 아니라 붙이지 않는다
CONDITION_KINDS = frozenset({"chronic_disease", "other_medical"})


def symptom_days(
    observations: Sequence[HealthObservation], *, today: date, timezone: tzinfo
) -> dict[str, set[date]]:
    """증상 코드 → 7일 창 안에서 기록된 날들. 사전 밖 증상은 코드가 없어 세지 않는다."""
    first = today - timedelta(days=REPEAT_WINDOW_DAYS - 1)
    found: dict[str, set[date]] = {}
    for record in observations:
        in_window = [day for day in observation_days(record, timezone) if first <= day <= today]
        if not in_window:
            continue
        for code in symptom_codes_of(record.symptoms):
            found.setdefault(code, set()).update(in_window)
    return found


@dataclass(frozen=True)
class ConditionMatch:
    labels: tuple[str, ...]  # 보호자가 등록한 이름 원문, 등록 순
    guidances: tuple[Guidance, ...]


def lookup_condition_guidance(
    symptom_code: str,
    entries: Sequence[HealthSafetyEntry],
    guidance: Mapping[tuple[str, str], Guidance],
) -> ConditionMatch | None:
    """등록된 질환 중 (질환 × 증상) 행이 있는 것. 없으면 None.

    질환 이름만 맞춘다 — 증상에서 질환을 찾지 않는다. 알레르기와 철회한 항목은 보지 않고, 사전에
    없는 이름은 코드로 바꾸지 않는다. 모델은 이 함수를 보지 못한다.
    """
    labels: dict[str, None] = {}
    found: dict[tuple[str, str], Guidance] = {}
    for entry in entries:
        if entry.kind not in CONDITION_KINDS or entry.status != "active":
            continue
        for code in match_terms(entry.label, condition_codes().terms):
            row = guidance.get((code, symptom_code))
            if row is not None:
                labels.setdefault(entry.label)
                found.setdefault((code, symptom_code), row)
    if not labels:
        return None
    return ConditionMatch(labels=tuple(labels), guidances=tuple(found.values()))


async def _registered_conditions(context: HealthContext) -> list[HealthSafetyEntry] | None:
    """등록된 건강 항목. 못 읽으면 None — "등록된 질환 없음" 과 구분한다."""
    try:
        return await context.ports.safety.health_safety(child_id=context.child_id)
    except SafetyLookupError:
        return None


async def check_symptom_repetition(
    context: HealthContext, *, guidance: Mapping[tuple[str, str], Guidance] | None = None
) -> tuple[Readout, ...]:
    """반복이 선 증상마다 안내. 동의가 없으면 읽지도 않는다.

    (질환 × 증상) 행이 있으면 질환 이름을 붙인 문장과 출처가 달린 안내 카드가 같이 나가고, 아니면
    기본 문장만 나간다. 건강 항목은 반복이 선 증상이 있고 안내 행이 있을 때만 읽는다.
    """
    ports = context.ports
    if not await ports.consent.child_health_granted(child_id=context.child_id):
        return ()
    today = context.today
    observations = await ports.memory.health_observations(
        child_id=context.child_id,
        date_from=today - timedelta(days=REPEAT_WINDOW_DAYS - 1),
        date_to=today,
    )
    days = symptom_days(observations, today=today, timezone=context.timezone)
    book = symptom_codes()
    repeated = [
        (term.key, len(days[term.key]))  # 사전 순서 — 호출마다 같은 순서로 나간다
        for term in book.terms
        if len(days.get(term.key, ())) >= REPEAT_MIN_DAYS
    ]
    rows = condition_guidance() if guidance is None else guidance
    entries = await _registered_conditions(context) if repeated and rows else None

    readouts: list[Readout] = []
    for code, count in repeated:
        values = {"symptom": book.labels[code], "n": str(count)}
        match = lookup_condition_guidance(code, entries, rows) if entries else None
        if match is None:
            readouts.append(READOUTS.render(SYMPTOM_REPEAT, **values))
            continue
        # 템플릿이 '{condition}' 으로 따옴표를 두르므로 둘 이상이면 사이에 닫고 여는 따옴표를 넣는다
        condition = "' · '".join(match.labels)
        readouts.append(READOUTS.render(SYMPTOM_REPEAT_CONDITION, condition=condition, **values))
        readouts.extend(_card(row) for row in match.guidances)
    return tuple(readouts)


def _card(row: Guidance) -> Readout:
    return Readout(
        kind="condition_guidance",
        title=f"출처: {row.publisher} 「{row.source_title}」",
        body=row.text,
        authored_by="code",
    )
