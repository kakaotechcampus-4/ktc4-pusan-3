import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY, DATERANGE, UUID, Range
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class ConfidenceSource(enum.StrEnum):
    INSTITUTION_NOTICE = "institution_notice"
    PARENT_DIRECT = "parent_direct"
    PARENT_HEDGED = "parent_hedged"
    PARENT_HEARSAY = "parent_hearsay"


class ObservationStatus(enum.StrEnum):
    """검색과 집계를 따로 끊는다. 두 곳의 포함 여부가 값마다 다르다.

      active       Memory Search O · Curator 집계 O
      stand_alone  Memory Search O · Curator 집계 X
      inactive     Memory Search X · Curator 집계 X

    stand_alone 은 Correction once_only("이번만 그랬어요") 가 만드는 상태다.
    관찰 자체는 실제로 있었던 일이라 검색에는 남기고, 성향으로 집계되는 것만 막는다.
    active / inactive 둘뿐이면 이 둘을 한 번에 빼거나 한 번에 남길 수밖에 없다.

    누가 이 값을 읽는지는 아직 코드에 없다 — 검색 필터와 Curator 집계는 후속 이슈다.
    """

    ACTIVE = "active"
    STAND_ALONE = "stand_alone"
    INACTIVE = "inactive"


class EngagementLevel(enum.StrEnum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class HealthSeverity(enum.StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    EMERGENCY = "emergency"


class RoutineCategory(enum.StrEnum):
    SELF_CARE = "self_care"  # 양치 / 옷 입기 / 손 씻기
    MEALTIME = "mealtime"  # 식사 도구 / 식사 태도 — 무엇을 먹었는지는 food
    HOUSEHOLD_TASK = "household_task"  # 장난감 정리 / 심부름
    SOCIAL_MANNER = "social_manner"  # 인사 / 차례 지키기
    HABIT = "habit"  # 손톱 물어뜯기 / 손가락 빨기 (증상 X, 단순 버릇)
    TRANSITION = "transition"  # 등원 준비 / 잠자리 들기 / 놀이 끝내기


class AssistanceLevel(enum.StrEnum):
    INDEPENDENT = "independent"
    VERBAL_PROMPT = "verbal_prompt"
    PARTIAL_ASSIST = "partial_assist"
    FULL_ASSIST = "full_assist"


class CompletionStatus(enum.StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    REFUSED = "refused"
    INTERRUPTED = "interrupted"


confidence_source = enum_col_py(ConfidenceSource, name="confidence_source")
observation_status = enum_col_py(ObservationStatus, name="observation_status")
engagement_level = enum_col_py(EngagementLevel, name="engagement_level")
routine_category = enum_col_py(RoutineCategory, name="routine_category")
assistance_level = enum_col_py(AssistanceLevel, name="assistance_level")
completion_status = enum_col_py(CompletionStatus, name="completion_status")

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
    source_writer: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
    source_notice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ObservationFood(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_food"
    __table_args__ = (Index("ix_observation_food_child_status", "child_id", "status"),)

    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaction: Mapped[str | None] = mapped_column(Text, nullable=True)


class ObservationEducation(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_education"
    __table_args__ = (Index("ix_observation_education_child_status", "child_id", "status"),)

    topic: Mapped[str] = mapped_column(Text, nullable=False)  # 사람이 읽는 원문. subject 와 별개
    session_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_level: Mapped[EngagementLevel | None] = mapped_column(
        engagement_level, nullable=True
    )


class ObservationActivity(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_activity"
    __table_args__ = (Index("ix_observation_activity_child_status", "child_id", "status"),)

    activity: Mapped[str] = mapped_column(Text, nullable=False)  # 사람이 읽는 원문. subject 와 별개
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    companions: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_level: Mapped[EngagementLevel | None] = mapped_column(
        engagement_level, nullable=True
    )


class ObservationRoutine(Base, UUIDPk, Timestamps, ObservationCommon):
    __tablename__ = "observation_routine"
    __table_args__ = (Index("ix_observation_routine_child_status", "child_id", "status"),)

    routine_category: Mapped[RoutineCategory] = mapped_column(routine_category, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    assistance_level: Mapped[AssistanceLevel | None] = mapped_column(
        assistance_level, nullable=True
    )
    completion_status: Mapped[CompletionStatus | None] = mapped_column(
        completion_status, nullable=True
    )
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)


class ObservationHealth(Base, UUIDPk, Timestamps):
    """승격 파이프라인 밖. ObservationCommon 을 상속하지 않는다."""

    __tablename__ = "observation_health"
    __table_args__ = (Index("ix_observation_health_child_status", "child_id", "status"),)

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_source: Mapped[ConfidenceSource] = mapped_column(confidence_source, nullable=False)
    status: Mapped[ObservationStatus] = mapped_column(
        observation_status, nullable=False, server_default="active"
    )
    observed_range: Mapped[Range[date]] = mapped_column(DATERANGE, nullable=False)
    source_writer: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
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
    observed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
