import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class SuggestionAgent(enum.StrEnum):
    FOOD = "food"
    ACTIVITY = "activity"
    EDUCATION = "education"
    HEALTH = "health"


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
    # [{"kind": "observation_food" | "observation_health" | "observation_education"
    #           | "observation_activity" | "profile_affinity", "id": "<uuid>"}, ...]
    # 형태 검증 CHECK 는 마이그레이션에서 is_valid_refs() 로 붙인다
    source_refs: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, server_default="[]")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
