"""제안 표시·피드백과 관찰 상세의 근거 역조회."""

import base64
import json
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvent,
    SuggestionEvidence,
    SuggestionFeedback,
    SuggestionKind,
    SuggestionStatus,
)


async def create_suggestion(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    agent: SuggestionAgent,
    kind: SuggestionKind,
    content: str,
    reason: str | None,
    citations: list[dict],
    expires_at: datetime,
    allergens: Sequence[str] = (),
    items: Sequence[str] = (),
) -> Suggestion:
    """Agent 결과 저장. 일반/개인화 판정과 근거 필수 여부는 호출 계층에서 검증한다.

    `citations` · `allergens` · `items` 는 `SuggestionDraft.to_payload()` 의 같은 이름 칸을
    그대로 받는다. `citations` 는 `source_kind` · `source_id` · `note` 세 칸이다.
    """
    row = Suggestion(
        child_id=child_id,
        agent=SuggestionAgent(agent),
        kind=SuggestionKind(kind),
        content=content,
        reason=reason,
        allergens=list(allergens),
        items=list(items),
        expires_at=expires_at,
        status=SuggestionStatus.DRAFT,
    )
    session.add(row)
    await session.flush()
    for item in citations:
        session.add(
            SuggestionEvidence(
                suggestion_id=row.id,
                source_kind=item["source_kind"],
                source_id=uuid.UUID(str(item["source_id"])),
                note=item["note"],
            )
        )
    await session.flush()
    return row


async def find_suggestion(
    session: AsyncSession, *, child_id: uuid.UUID, suggestion_id: uuid.UUID
) -> Suggestion | None:
    return await session.scalar(
        select(Suggestion).where(Suggestion.child_id == child_id, Suggestion.id == suggestion_id)
    )


def encode_cursor(created_at: datetime, suggestion_id: uuid.UUID) -> str:
    payload = {"c": created_at.isoformat(), "id": str(suggestion_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    payload = json.loads(base64.urlsafe_b64decode(cursor))
    return datetime.fromisoformat(payload["c"]), uuid.UUID(payload["id"])


async def list_suggestions(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    status: SuggestionStatus | None = None,
    kind: SuggestionKind | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> tuple[list[Suggestion], str | None]:
    """커서 페이징으로 추천 목록을 반환한다. (items, next_cursor)."""
    stmt = select(Suggestion).where(Suggestion.child_id == child_id)
    if kind is not None:
        stmt = stmt.where(Suggestion.kind == SuggestionKind(kind))
    if status is not None:
        stmt = stmt.where(Suggestion.status == SuggestionStatus(status))
    # draft 조회 시 만료된 것은 제외 (lazy expiration)
    if status == SuggestionStatus.DRAFT:
        stmt = stmt.where(Suggestion.expires_at > func.now())
    if cursor is not None:
        cur_created, cur_id = decode_cursor(cursor)
        stmt = stmt.where(
            (Suggestion.created_at < cur_created)
            | ((Suggestion.created_at == cur_created) & (Suggestion.id < cur_id))
        )
    stmt = stmt.order_by(Suggestion.created_at.desc(), Suggestion.id.desc()).limit(limit + 1)
    rows = list((await session.scalars(stmt)).all())

    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    else:
        next_cursor = None

    return rows, next_cursor


async def set_feedback(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    feedback: SuggestionFeedback,
) -> Suggestion | None:
    """피드백은 제안만 바꾼다. 관찰/성향 상태는 여기서 건드리지 않는다."""
    return await session.scalar(
        update(Suggestion)
        .where(Suggestion.child_id == child_id, Suggestion.id == suggestion_id)
        .values(feedback=SuggestionFeedback(feedback))
        .returning(Suggestion)
    )


async def approve_suggestions(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    suggestion_ids: list[uuid.UUID],
) -> list[Suggestion]:
    """draft → approved. 호출자가 소유·상태·만료를 사전 검증한 뒤 부른다.

    DB 에서 한 번에 UPDATE — draft + 만료 전인 것만 바꾼다.
    사전 검증과 UPDATE 사이에 상태가 바뀌었으면 개수 불일치로 빈 목록을 돌려준다.
    """
    result = await session.execute(
        update(Suggestion)
        .where(
            Suggestion.child_id == child_id,
            Suggestion.id.in_(suggestion_ids),
            Suggestion.status == SuggestionStatus.DRAFT,
            Suggestion.expires_at > func.now(),
        )
        .values(status=SuggestionStatus.APPROVED)
        .returning(Suggestion)
    )
    approved = list(result.scalars().all())
    if len(approved) != len(suggestion_ids):
        return []
    return approved


async def link_suggestions_to_event(
    session: AsyncSession,
    *,
    suggestion_ids: list[uuid.UUID],
    event_id: uuid.UUID,
) -> None:
    """추천 ↔ 일정 연결 INSERT."""
    for sid in suggestion_ids:
        session.add(SuggestionEvent(suggestion_id=sid, event_id=event_id))
    await session.flush()


async def is_any_linked(
    session: AsyncSession, *, suggestion_ids: list[uuid.UUID]
) -> bool:
    """suggestion_ids 중 이미 일정에 연결된 것이 있는지."""
    stmt = select(
        exists().where(SuggestionEvent.suggestion_id.in_(suggestion_ids))
    )
    return bool(await session.scalar(stmt))


# 아이 기록 9종. 문서 행(food_doc 등)은 응답 evidence에서 제외한다.
# agents/common/refs.py ChildRecordKind와 같은 목록.
_CHILD_RECORD_KINDS = frozenset((
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "profile_affinity",
    "child_growth_log",
    "notice",
    "daycare_meal",
))


async def list_evidence(
    session: AsyncSession, *, suggestion_id: uuid.UUID
) -> list[SuggestionEvidence]:
    """추천 한 건의 근거 행 (아이 기록만, 문서 행 제외)."""
    stmt = (
        select(SuggestionEvidence)
        .where(
            SuggestionEvidence.suggestion_id == suggestion_id,
            SuggestionEvidence.source_kind.in_(_CHILD_RECORD_KINDS),
        )
        .order_by(SuggestionEvidence.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def list_evidence_batch(
    session: AsyncSession, *, suggestion_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SuggestionEvidence]]:
    """여러 추천의 근거를 한 번에 조회한다 (아이 기록만, 문서 행 제외)."""
    if not suggestion_ids:
        return {}
    stmt = (
        select(SuggestionEvidence)
        .where(
            SuggestionEvidence.suggestion_id.in_(suggestion_ids),
            SuggestionEvidence.source_kind.in_(_CHILD_RECORD_KINDS),
        )
        .order_by(SuggestionEvidence.suggestion_id, SuggestionEvidence.created_at)
    )
    rows = list((await session.scalars(stmt)).all())
    result: dict[uuid.UUID, list[SuggestionEvidence]] = {sid: [] for sid in suggestion_ids}
    for row in rows:
        result[row.suggestion_id].append(row)
    return result


async def list_suggestions_using_observation(
    session: AsyncSession, *, child_id: uuid.UUID, kind: str, observation_id: uuid.UUID
) -> list[Suggestion]:
    """그 관찰을 근거로 쓴 추천. 근거는 `suggestion_evidence` 행이다."""
    stmt = (
        select(Suggestion)
        .join(SuggestionEvidence, SuggestionEvidence.suggestion_id == Suggestion.id)
        .where(
            Suggestion.child_id == child_id,
            SuggestionEvidence.source_kind == kind,
            SuggestionEvidence.source_id == observation_id,
        )
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
    )
    return list((await session.scalars(stmt)).all())
