import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class SuggestionAgent(enum.StrEnum):
    FOOD = "food"
    ACTIVITY = "activity"
    GROWTH = "growth"
    HEALTH = "health"


class SuggestionKind(enum.StrEnum):
    """아이 기록 근거가 있으면 personalized, 없으면 general.

    코드가 정한다. 근거 0행인 general은 정상 경로이고, 개인화로 집계되지만 않으면 된다.
    """

    GENERAL = "general"
    PERSONALIZED = "personalized"


class SuggestionStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SuggestionFeedback(enum.StrEnum):
    LIKED = "liked"
    DISLIKED = "disliked"
    NOT_ACTED = "not_acted"


class Suggestion(Base, UUIDPk, Timestamps):
    __tablename__ = "suggestion"

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[SuggestionAgent] = mapped_column(
        enum_col_py(SuggestionAgent, name="suggestion_agent"),
        nullable=False,
    )
    kind: Mapped[SuggestionKind] = mapped_column(
        enum_col_py(SuggestionKind, name="suggestion_kind"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 그 추천에 들어 있는 알레르기 항목의 정식 명칭. 상태(없음/모름)는 담지 않는다 —
    # 추천이 draft로 사는 24시간 사이에 보호자가 답할 수 있어서
    # 승인할 때 health_safety를 다시 읽는다.
    allergens: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    # 일정으로 만들 때 준비물(event_item.item_name) 하나씩. 알레르기 판단에는 쓰지 않는다.
    items: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    status: Mapped[SuggestionStatus] = mapped_column(
        enum_col_py(SuggestionStatus, name="suggestion_status"),
        nullable=False,
        server_default="draft",
    )
    feedback: Mapped[SuggestionFeedback | None] = mapped_column(
        enum_col_py(SuggestionFeedback, name="suggestion_feedback"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SuggestionEvent(Base):
    """추천 ↔ 일정 연결. 한 추천은 일정 하나에만 연결된다.

    suggestion_id UNIQUE — 같은 추천이 일정 두 개에 걸리지 않는다.
    event_id ON DELETE CASCADE — 일정 삭제(hard delete) 시 연결만 사라지고
    추천은 approved 로 남는다 (멘토 합의).
    """

    __tablename__ = "suggestion_event"
    __table_args__ = (
        sa.UniqueConstraint("suggestion_id", name="uq_suggestion_event_suggestion_id"),
        # 일정 삭제(CASCADE) 때 event_id 로 찾는다 — 마이그레이션 f8a2b3c4d5e6 과 같은 이름
        sa.Index("ix_suggestion_event_event_id", "event_id"),
    )

    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suggestion.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SuggestionEvidence(Base):
    """그 추천이 쓴 근거 한 줄.

    문서 행(`*_doc`)만 달고 나간 개인화 추천은 근거 0행과 같다.
    """

    __tablename__ = "suggestion_evidence"
    __table_args__ = (Index("ix_suggestion_evidence_source_kind", "source_kind"),)

    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suggestion.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 다형 참조라 FK가 아니다. 대상이 있는지와 같은 child_id 인지는 서버가 본다.
    source_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # 그 행에서 추천 근거로 채택한 내용. Agent가 쓰고 보호자 화면에 나간다.
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
