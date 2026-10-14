"""추천 근거의 원본 읽기 — 화면에 나가는 label · 날짜 · 낡음 (#323 최소분 · #249).

`suggestion_evidence` 에는 종류 · id · note 만 있다. 05 · 목록 · 피드백 응답이 사람이 읽을 이름과
날짜를 싣도록 원본 행을 한 번에 읽는다. 낡음은 `rules/staleness.is_stale`(달력 6개월) —
기억 카드(`Affinity.is_stale`)와 같은 판정이다.

- 원본이 없거나 보호자가 지운 기록(`deleted`)은 빠진다. 지운 기록의 내용을 근거로 다시 보여
  주지 않는다. 그래서 개인화 추천의 보이는 근거가 0행이 될 수 있고, 05 는 그 추천을 내지 않는다.
- 건강 관찰은 이름이 없는 기록이다(`subject` 칸이 없다). 증상 · 원문을 근거 이름으로 되풀이하지
  않고 고정 이름을 쓴다.
- 문서 행 · 아직 테이블이 없는 종류(notice · daycare_meal)는 여기서 다루지 않는다.
- `confidence_source` 는 관찰만 있다. 관심 · 성장 기록은 "" (#323 나머지).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import ChildGrowthLog
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
    ObservationStatus,
)
from app.domains.memory.profile.models import ProfileAffinity
from app.domains.suggestion.models import SuggestionEvidence
from app.rules.staleness import is_stale

_OBSERVATIONS = {
    "observation_food": ObservationFood,
    "observation_health": ObservationHealth,
    "observation_education": ObservationEducation,
    "observation_activity": ObservationActivity,
    "observation_routine": ObservationRoutine,
}

# 성장 기록은 숫자를 근거 이름에 싣지 않는다 — 평가처럼 읽히는 문장을 만들지 않는다 (루트 §2)
GROWTH_LOG_LABEL = "키 · 몸무게 기록"
# 건강 관찰은 내용을 근거 이름에 싣지 않는다 — 증상 문장이 다른 화면에 되풀이되지 않게
HEALTH_LABEL = "건강 기록"


@dataclass(frozen=True)
class EvidenceView:
    source_kind: str
    source_id: uuid.UUID
    note: str
    label: str
    observed_to: date
    confidence_source: str
    is_stale: bool


async def describe_evidence(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    rows: Sequence[SuggestionEvidence],
    today: date,
) -> dict[uuid.UUID, list[EvidenceView]]:
    found: dict[tuple[str, uuid.UUID], tuple[str, date, str]] = {}
    for kind, model in _OBSERVATIONS.items():
        ids = [r.source_id for r in rows if r.source_kind == kind]
        if not ids:
            continue
        stmt = select(model).where(
            model.id.in_(ids),
            model.child_id == child_id,
            model.status != ObservationStatus.DELETED,
        )
        for obs in (await session.scalars(stmt)).all():
            # daterange 의 upper 는 열린 끝이라 하루를 뺀 날이 마지막으로 본 날이다
            observed_to = obs.observed_range.upper - timedelta(days=1)
            label = HEALTH_LABEL if kind == "observation_health" else obs.subject
            found[(kind, obs.id)] = (label, observed_to, obs.confidence_source.value)
    affinity_ids = [r.source_id for r in rows if r.source_kind == "profile_affinity"]
    if affinity_ids:
        stmt = select(ProfileAffinity).where(
            ProfileAffinity.id.in_(affinity_ids), ProfileAffinity.child_id == child_id
        )
        for aff in (await session.scalars(stmt)).all():
            found[("profile_affinity", aff.id)] = (aff.merge_key, aff.last_observed_on, "")
    growth_ids = [r.source_id for r in rows if r.source_kind == "child_growth_log"]
    if growth_ids:
        stmt = select(ChildGrowthLog).where(
            ChildGrowthLog.id.in_(growth_ids), ChildGrowthLog.child_id == child_id
        )
        for log in (await session.scalars(stmt)).all():
            found[("child_growth_log", log.id)] = (GROWTH_LOG_LABEL, log.measured_on, "")

    views: dict[uuid.UUID, list[EvidenceView]] = {r.suggestion_id: [] for r in rows}
    for r in rows:
        hit = found.get((r.source_kind, r.source_id))
        if hit is None:
            continue
        label, observed_to, source = hit
        views[r.suggestion_id].append(
            EvidenceView(
                source_kind=r.source_kind,
                source_id=r.source_id,
                note=r.note,
                label=label,
                observed_to=observed_to,
                confidence_source=source,
                is_stale=is_stale(observed_to, today),
            )
        )
    return views
