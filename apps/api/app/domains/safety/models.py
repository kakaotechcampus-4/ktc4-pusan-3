import uuid

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.memory.enums import enum_col
from app.infra.db.base import Base, Timestamps, UUIDPk


class HealthSafety(Base, UUIDPk, Timestamps):
    __tablename__ = "health_safety"

    child_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(
        enum_col(
            "allergy",
            "chronic_disease",
            "dietary_restriction",
            "behavioral",
            "environmental",
            "other_medical",
            name="safety_kind",
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(
        enum_col("mild", "moderate", "severe", "anaphylaxis", name="safety_severity"),
        nullable=True,
    )
    reactions: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    management: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(
        enum_col("active", "retracted", name="safety_state"),
        nullable=False,
        server_default="active",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
