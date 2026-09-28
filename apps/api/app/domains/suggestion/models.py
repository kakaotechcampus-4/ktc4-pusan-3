import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
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
    # 인용할 때 읽은 원본의 시각. 아이 기록은 그 행의 updated_at, 문서 행은 written_at.
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 그 행에서 추천 근거로 채택한 내용. Agent가 쓰고 보호자 화면에 나간다.
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
