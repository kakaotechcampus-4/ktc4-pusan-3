"""제안 표시·피드백과 관찰 상세의 근거 역조회."""

import uuid
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
) -> Suggestion:
    """Agent 결과 저장. 일반/개인화 판정과 근거 필수 여부는 호출 계층에서 검증한다.

    `citations`는 `SuggestionDraft.to_payload()`의 `citations`를 그대로 받는다 —
    `source_kind` · `source_id` · `note` 세 칸이다.
    """
    row = Suggestion(
        child_id=child_id,
        agent=SuggestionAgent(agent),
        kind=SuggestionKind(kind),
        content=content,
        reason=reason,
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


async def list_suggestions(
    session: AsyncSession, *, child_id: uuid.UUID, status: SuggestionStatus | None = None
) -> list[Suggestion]:
    stmt = select(Suggestion).where(Suggestion.child_id == child_id)
    if status is not None:
        stmt = stmt.where(Suggestion.status == SuggestionStatus(status))
    # draft 조회 시 만료된 것은 제외 (lazy expiration)
    if status == SuggestionStatus.DRAFT:
        stmt = stmt.where(Suggestion.expires_at > func.now())
    return list(
        (
            await session.scalars(stmt.order_by(Suggestion.created_at.desc(), Suggestion.id.desc()))
        ).all()
    )


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
