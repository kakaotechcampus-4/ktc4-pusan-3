import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col


class Suggestion(Base, UUIDPk, Timestamps):
    __tablename__ = "suggestion"

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[str] = mapped_column(
        enum_col("food", "activity", "education", "health", name="suggestion_agent"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        enum_col("draft", "approved", "rejected", "expired", name="suggestion_status"),
        nullable=False,
        server_default="draft",
    )
    feedback: Mapped[str | None] = mapped_column(
        enum_col("liked", "disliked", "not_acted", name="suggestion_feedback"),
        nullable=True,
    )
    # [{"kind": "observation_food" | "observation_health" | "observation_education"
    #           | "observation_activity" | "profile_affinity", "id": "<uuid>"}, ...]
    # 형태 검증 CHECK 는 마이그레이션에서 is_valid_refs() 로 붙인다
    source_refs: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, server_default="[]")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
