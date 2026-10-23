"""증상 표준 코드와 에피소드 — 반복 판정과 진료 준비 타임라인이 같은 어휘 · 날짜 규칙 위에 선다.

- `normalize_symptom_term` · `symptom_codes_of`  보호자가 쓴 말 → 사전 코드. 사전에 없으면 코드로
  바꾸지 않는다(원문은 호출부가 그대로 둔다). 원문끼리 같다고 묶지도 않는다.
- `observation_days`  기록 한 건이 센 날들. 아이 타임존의 달력 날짜다 — hook 이 이 날로 세고,
  에피소드도 같은 날로 묶는다.
- `group_symptom_episodes`  같은 코드가 48시간 안에 이어지면 한 에피소드. 타임라인 표시용이고 반복
  판정에는 쓰지 않는다(사흘 내리 발열은 에피소드 1건이지만 반복 판정에는 3일이다).

원인 · 중증도를 말하지 않는다. 모델 0회.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo
from uuid import UUID

from app.agents.health.reference import symptom_codes
from app.agents.health.store.ports import HealthObservation
from app.rules.term_match import match_terms

EPISODE_GAP = timedelta(hours=48)  # H-9 설정값


def normalize_symptom_term(text: str) -> str | None:
    """사전 코드 하나. 사전에 없거나 한 문장에 증상이 둘 이상이면 None 이다.

    둘 이상이면 하나만 골라 돌려주지 않는다 — 나머지 증상이 조용히 사라진다. 여러 개를 다 받으려면
    `symptom_codes_of` 를 쓴다.
    """
    found = match_terms(text, symptom_codes().terms)
    return found[0] if len(found) == 1 else None


def symptom_codes_of(symptoms: Sequence[str]) -> tuple[str, ...]:
    """증상 원문들에서 걸린 사전 코드 전부. 순서를 지키고 중복을 뺀다."""
    codes: dict[str, None] = {}
    for text in symptoms:
        for code in match_terms(text, symptom_codes().terms):
            codes.setdefault(code)
    return tuple(codes)


def observation_days(observation: HealthObservation, timezone: tzinfo) -> tuple[date, ...]:
    """기록 한 건이 센 날들.

    몇 시인지 말한 기록은 그 시각이 아이 타임존에서 속한 날이다(DB 의 UTC 오프셋 그대로 읽으면
    자정 부근이 하루 어긋난다). 아니면 관찰한 날들이고, "사흘째 기침해" 처럼 기간이면 그 날들이다.
    """
    if observation.observed_time is not None:
        return (observation.observed_time.astimezone(timezone).date(),)
    span = max((observation.observed_to - observation.observed_from).days, 0)
    return tuple(observation.observed_from + timedelta(days=n) for n in range(span + 1))


@dataclass(frozen=True)
class Episode:
    code: str
    starts_on: date
    ends_on: date
    days: int  # 기록된 날 수
    observation_ids: tuple[UUID, ...]


def group_symptom_episodes(
    observations: Sequence[HealthObservation], *, timezone: tzinfo, gap: timedelta = EPISODE_GAP
) -> tuple[Episode, ...]:
    """증상 코드마다, 기록된 날 사이가 `gap` 이내로 이어지면 한 에피소드. 날 단위로 본다."""
    by_code: dict[str, dict[date, list[UUID]]] = {}
    for record in observations:
        days = observation_days(record, timezone)
        for code in symptom_codes_of(record.symptoms):
            ids_by_day = by_code.setdefault(code, {})
            for day in days:
                ids = ids_by_day.setdefault(day, [])
                if record.id not in ids:
                    ids.append(record.id)

    episodes: list[Episode] = []
    for code in (term.key for term in symptom_codes().terms):
        ids_by_day = by_code.get(code)
        if not ids_by_day:
            continue
        run: list[date] = []
        for day in sorted(ids_by_day):
            if run and timedelta(days=(day - run[-1]).days) > gap:
                episodes.append(_episode(code, run, ids_by_day))
                run = []
            run.append(day)
        episodes.append(_episode(code, run, ids_by_day))
    return tuple(sorted(episodes, key=lambda e: (e.starts_on, e.code)))


def _episode(code: str, days: list[date], ids_by_day: dict[date, list[UUID]]) -> Episode:
    ids: dict[UUID, None] = {}
    for day in days:
        for record_id in ids_by_day[day]:
            ids.setdefault(record_id)
    return Episode(
        code=code,
        starts_on=days[0],
        ends_on=days[-1],
        days=len(days),
        observation_ids=tuple(ids),
    )
