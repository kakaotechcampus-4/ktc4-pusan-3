"""제안 표시·피드백과 관찰 상세의 근거 역조회."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
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
    `source_kind` · `source_id` · `source_updated_at` · `note` 네 칸이다.
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
                source_updated_at=item["source_updated_at"],
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
