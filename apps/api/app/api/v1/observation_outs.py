"""관찰 행 → 화면 모양 — 07 기록 탭 · 09 캘린더 · 고치기 응답 · 04 run 의 `saved` (#471).

라우터 밖에 두는 이유는 백그라운드 러너(`api/runs/`)도 쓰기 때문이다. 같은 함수를 지나야
04 에서 "저장됨" 으로 본 기록과 07 기록 탭의 기록이 같은 모양이 된다.

🚨 건강 관찰은 `ObservationHealthOut` 으로 바꾼다 — `subject` · `polarity` · `affinity` 키가
   아예 없다.
   목록 · 상세 · 캘린더는 `VISIBLE_DOMAINS` 가 앞에서 걸러서 여기까지 오지 않는다 (#259).
   여기까지 오는 건 04 run 의 `saved` 뿐이다.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import KST
from app.api.v1.schemas.observations import (
    LinkedAffinityOut,
    ObservationHealthOut,
    ObservationOut,
    SourceWriterOut,
)
from app.domains.identity.models import Parent
from app.domains.memory.observation.repository import ObservationDomain, ObservationRecord
from app.domains.memory.profile.models import ProfileAffinity
from app.rules.observed_label import observed_label

# 응답의 고정 칸으로 따로 싣거나, 화면에 내리지 않는 칸. 나머지가 domain_fields 다.
_NOT_DOMAIN_FIELDS = frozenset(
    {
        "subject",
        "polarity",
        "confidence_source",
        "status",
        "source_writer",
        "source_notice_id",
        "updated_at",
        "embedding",
        "affinity_id",
        "strong_signals",
    }
)


async def to_observation_outs(
    session: AsyncSession, records: list[ObservationRecord], *, today: date
) -> list[ObservationOut | ObservationHealthOut]:
    """묶인 기억과 적은 사람은 한 번씩 모아 읽는다 — 줄마다 조회하지 않는다."""
    affinity_ids = {r.fields["affinity_id"] for r in records if r.fields.get("affinity_id")}
    writer_ids = {r.fields["source_writer"] for r in records if r.fields.get("source_writer")}
    affinities = (
        {
            a.id: a
            for a in await session.scalars(
                select(ProfileAffinity).where(ProfileAffinity.id.in_(affinity_ids))
            )
        }
        if affinity_ids
        else {}
    )
    writers = (
        {
            p.id: p.nickname
            for p in await session.scalars(select(Parent).where(Parent.id.in_(writer_ids)))
            if p.nickname
        }
        if writer_ids
        else {}
    )
    return [_observation_out(r, affinities, writers, today=today) for r in records]


def _observation_out(
    record: ObservationRecord,
    affinities: dict[uuid.UUID, ProfileAffinity],
    writers: dict[uuid.UUID, str],
    *,
    today: date,
) -> ObservationOut | ObservationHealthOut:
    fields = record.fields
    observed_to = record.observed_range.upper - timedelta(days=1)
    writer_id = fields.get("source_writer")
    common = {
        "id": str(record.id),
        "child_id": str(record.child_id),
        "kind": f"observation_{record.domain.value}",
        "raw_text": record.raw_text,
        "confidence_source": fields["confidence_source"].value,
        "status": fields["status"].value,
        "observed_from": record.observed_range.lower,
        "observed_to": observed_to,
        "observed_label": observed_label(observed_to, today=today),
        "domain_fields": _domain_fields(fields),
        "source_writer": (
            SourceWriterOut(parent_id=str(writer_id), nickname=writers[writer_id])
            if writer_id in writers
            else None
        ),
        "created_at": record.created_at,
    }
    if record.domain is ObservationDomain.HEALTH:
        return ObservationHealthOut(**common)

    affinity = affinities.get(fields.get("affinity_id"))
    return ObservationOut(
        **common,
        subject=fields["subject"],
        polarity=fields["polarity"],
        strong_signals=list(fields.get("strong_signals") or []),
        affinity=(
            LinkedAffinityOut(
                id=str(affinity.id), merge_key=affinity.merge_key, state=affinity.state.value
            )
            if affinity
            else None
        ),
    )


def _domain_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _field_value(value) for key, value in fields.items() if key not in _NOT_DOMAIN_FIELDS
    }


def _field_value(value: Any) -> Any:
    """enum 은 값으로, 시각은 서비스 시간대(KST)로, 체온은 숫자로.

    🚨 DB 는 timestamptz 를 UTC 로 돌려준다(health `observed_time` · `measured_at`). 그대로 실으면
       보호자가 말한 밤 9시가 `…T12:00:00Z` 로 보인다 (#471 검토).
    🚨 numeric(3,1) 은 Decimal 로 온다(health `temperature`). JSON 으로 바꾸면 "38.5" 글자가 된다 —
       소수 첫째 자리까지라 float 로 옮겨도 값이 바뀌지 않는다 (#557).
    """
    if isinstance(value, datetime):
        return value.astimezone(KST)
    if isinstance(value, Decimal):
        return float(value)
    return value.value if hasattr(value, "value") else value
