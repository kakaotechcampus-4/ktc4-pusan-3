import uuid
from datetime import date

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, Float, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.memory.enums import memory_domain, profile_state
from app.infra.db.base import Base, Timestamps, UUIDPk


class ProfileAffinity(Base, UUIDPk, Timestamps):
    __tablename__ = "profile_affinity"

    child_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    merge_key: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    domain: Mapped[str] = mapped_column(memory_domain, nullable=False)
    state: Mapped[str] = mapped_column(
        profile_state, nullable=False, server_default="candidate"
    )
    polarity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    strength: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.3")
    last_observed_on: Mapped[date] = mapped_column(Date, nullable=False)
