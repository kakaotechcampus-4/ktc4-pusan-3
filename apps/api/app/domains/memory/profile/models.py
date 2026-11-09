import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class MemoryDomain(enum.StrEnum):
    FOOD = "food"
    ACTIVITY = "activity"
    EDUCATION = "education"


class ProfileState(enum.StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


memory_domain = enum_col_py(MemoryDomain, name="memory_domain")
profile_state = enum_col_py(ProfileState, name="profile_state")

# 선호(1) · 기피(-1) 만 있다. NULL 은 아직 모름(미상 · 동수 · 상충)이고 0 은 없다 (#287) —
# 좋다 · 싫다가 없는 관찰은 Curator 가 연결하지 않는다 (`docs/memory/curator-linking-v1.md` §1-1)
PROFILE_POLARITIES = (-1, 1)

# 파이썬 값에서 문장을 만든다. 🚨 마이그레이션 `496fd5c10cd1` 과 같은 글자다 —
# autogenerate 는 CHECK 를 비교하지 않는다
PROFILE_POLARITY_CHECK = (
    f"polarity IS NULL OR polarity IN ({', '.join(str(v) for v in PROFILE_POLARITIES)})"
)
PROFILE_CONFIRMED_POLARITY_CHECK = (
    f"state <> '{ProfileState.CONFIRMED.value}' OR polarity IS NOT NULL"
)


class ProfileAffinity(Base, UUIDPk, Timestamps):
    __tablename__ = "profile_affinity"
    __table_args__ = (
        CheckConstraint(PROFILE_POLARITY_CHECK, name="profile_affinity_polarity"),
        CheckConstraint(
            PROFILE_CONFIRMED_POLARITY_CHECK, name="profile_affinity_confirmed_polarity"
        ),
    )

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
    strength: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    last_observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    last_transition_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_transition_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
