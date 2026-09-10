import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY, DATERANGE, UUID, Range
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class ConfidenceSource(str, enum.Enum):
    INSTITUTION_NOTICE = "institution_notice"
    PARENT_DIRECT = "parent_direct"
    PARENT_HEDGED = "parent_hedged"
    PARENT_HEARSAY = "parent_hearsay"


class ObservationStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class EngagementLevel(str, enum.Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class HealthSeverity(str, enum.Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    EMERGENCY = "emergency"


confidence_source = enum_col_py(ConfidenceSource, name="confidence_source")
observation_status = enum_col_py(ObservationStatus, name="observation_status")
engagement_level = enum_col_py(EngagementLevel, name="engagement_level")

# generalization 은 유효 월령 72+ 라 1차 배포 타겟(≤71개월)에서 제외 확정
STRONG_SIGNALS = (
    "resistance_to_redirect",
    "self_initiated",
    "comparative_choice",
    "asks_questions",
    "role_extension",
)


class ObservationCommon:
    """food · education · activity 공통 컬럼. health 는 상속하지 않는다."""

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)  # 정규화 대상. 임베딩·병합 판정 입력
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    affinity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_affinity.id", ondelete="SET NULL"), nullable=True
    )
    polarity: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    strong_signals: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    confidence_source: Mapped[ConfidenceSource] = mapped_column(confidence_source, nullable=False)
    status: Mapped[ObservationStatus] = mapped_column(
        observation_status, nullable=False, server_default="active"
    )
    observed_range: Mapped[Range[date]] = mapped_column(DATERANGE, nullable=False)
    source_writer: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="RESTRICT"), nullable=False
    )
    source_notice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ObservationFood(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_food"

    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaction: Mapped[str | None] = mapped_column(Text, nullable=True)


class ObservationEducation(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_education"

    topic: Mapped[str] = mapped_column(Text, nullable=False)  # 사람이 읽는 원문. subject 와 별개
    session_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_level: Mapped[EngagementLevel | None] = mapped_column(
        engagement_level, nullable=True
    )


class ObservationActivity(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_activity"

    activity: Mapped[str] = mapped_column(Text, nullable=False)  # 사람이 읽는 원문. subject 와 별개
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    companions: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_level: Mapped[EngagementLevel | None] = mapped_column(
        engagement_level, nullable=True
    )


class ObservationHealth(Base, UUIDPk, Timestamps):
    """승격 파이프라인 밖. ObservationCommon 을 상속하지 않는다."""

    __tablename__ = "observation_health"

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_source: Mapped[ConfidenceSource] = mapped_column(confidence_source, nullable=False)
    status: Mapped[ObservationStatus] = mapped_column(
        observation_status, nullable=False, server_default="active"
    )
    observed_range: Mapped[Range[date]] = mapped_column(DATERANGE, nullable=False)
    source_writer: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="RESTRICT"), nullable=False
    )
    source_notice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symptom: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    severity: Mapped[HealthSeverity | None] = mapped_column(
        enum_col_py(HealthSeverity, name="health_severity"),
        nullable=True,
    )
    body_part: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspected_trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
