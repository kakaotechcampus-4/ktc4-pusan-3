import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.memory.enums import enum_col
from app.infra.db.base import Base, Timestamps, UUIDPk


class Suggestion(Base, UUIDPk, Timestamps):
    __tablename__ = "suggestion"

    child_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
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
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
