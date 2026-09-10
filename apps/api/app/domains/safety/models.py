import enum
import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class SafetyKind(str, enum.Enum):
    ALLERGY = "allergy"
    CHRONIC_DISEASE = "chronic_disease"
    DIETARY_RESTRICTION = "dietary_restriction"
    BEHAVIORAL = "behavioral"
    ENVIRONMENTAL = "environmental"
    OTHER_MEDICAL = "other_medical"


class SafetySeverity(str, enum.Enum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    ANAPHYLAXIS = "anaphylaxis"


class SafetyState(str, enum.Enum):
    ACTIVE = "active"
    RETRACTED = "retracted"


class HealthSafety(Base, UUIDPk, Timestamps):
    """안전·제약 정보. 3층 구조다.

    kind     대분류 — allergy / chronic_disease / ...
    category kind 별 중분류 — allergy 면 "식품"/"약물"/"환경", chronic_disease 면 "내분비" 등.
             kind 마다 값 집합이 달라 ENUM 이 아니다. 애플리케이션에서 검증한다.
    label    구체적 대상 — "우유" / "소아 당뇨" / "천식"
    aliases  label 의 별칭. 매칭 폭을 넓히기 위한 것

    안전 조회는 항상 state='active' 로 필터한다.
    알레르기 필터 로직은 아직 구체화 전이다.
    """

    __tablename__ = "health_safety"

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[SafetyKind] = mapped_column(
        enum_col_py(SafetyKind, name="safety_kind"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[SafetySeverity | None] = mapped_column(
        enum_col_py(SafetySeverity, name="safety_severity"),
        nullable=True,
    )
    reactions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    management: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[SafetyState] = mapped_column(
        enum_col_py(SafetyState, name="safety_state"),
        nullable=False,
        server_default="active",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
