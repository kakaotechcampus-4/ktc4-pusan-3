"""제안 표시·피드백과 관찰 상세의 근거 역조회."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionFeedback,
    SuggestionStatus,
)


async def create_suggestion(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    agent: SuggestionAgent,
    content: str,
    reason: str | None,
    source_refs: list[dict],
    expires_at: datetime,
) -> Suggestion:
    """Agent 결과 저장. 일반/개인화 판정과 근거 필수 여부는 호출 계층에서 검증한다."""
    row = Suggestion(
        child_id=child_id,
        agent=SuggestionAgent(agent),
        content=content,
        reason=reason,
        source_refs=source_refs,
        expires_at=expires_at,
        status=SuggestionStatus.DRAFT,
    )
    session.add(row)
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


async def list_suggestions_using_observation(
    session: AsyncSession, *, child_id: uuid.UUID, kind: str, observation_id: uuid.UUID
) -> list[Suggestion]:
    ref = [{"kind": kind, "id": str(observation_id)}]
    stmt = (
        select(Suggestion)
        .where(Suggestion.child_id == child_id, Suggestion.source_refs.contains(ref))
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
    )
    return list((await session.scalars(stmt)).all())
