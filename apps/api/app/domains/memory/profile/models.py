import enum
import uuid
from datetime import date

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, Float, ForeignKey, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class MemoryDomain(str, enum.Enum):
    FOOD = "food"
    ACTIVITY = "activity"
    EDUCATION = "education"


class ProfileState(str, enum.Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


memory_domain = enum_col_py(MemoryDomain, name="memory_domain")
profile_state = enum_col_py(ProfileState, name="profile_state")


class ProfileAffinity(Base, UUIDPk, Timestamps):
    __tablename__ = "profile_affinity"

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    merge_key: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    domain: Mapped[MemoryDomain] = mapped_column(memory_domain, nullable=False)
    state: Mapped[ProfileState] = mapped_column(
        profile_state, nullable=False, server_default="candidate"
    )
    polarity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    strength: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.3")
    last_observed_on: Mapped[date] = mapped_column(Date, nullable=False)
