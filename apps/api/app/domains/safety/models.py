import enum
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class SafetyKind(enum.StrEnum):
    ALLERGY = "allergy"
    CHRONIC_DISEASE = "chronic_disease"
    BEHAVIORAL = "behavioral"
    ENVIRONMENTAL = "environmental"
    OTHER_MEDICAL = "other_medical"


class SafetyCategory(enum.StrEnum):
    """알레르기의 분류. kind='allergy' 행만 갖는다.

    목록이라 쑥처럼 식품이면서 환경인 것을 한 행에 담는다.
    """

    FOOD = "food"
    DRUG = "drug"
    ENVIRONMENT = "environment"


class SafetySeverity(enum.StrEnum):
    """kind='allergy' 는 검사 결과의 Class 0~6, 나머지 kind 는 mild~anaphylaxis 를 쓴다."""

    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    ANAPHYLAXIS = "anaphylaxis"
    CLASS_0 = "class_0"
    CLASS_1 = "class_1"
    CLASS_2 = "class_2"
    CLASS_3 = "class_3"
    CLASS_4 = "class_4"
    CLASS_5 = "class_5"
    CLASS_6 = "class_6"


ALLERGY_SEVERITIES = frozenset(
    severity for severity in SafetySeverity if severity.startswith("class_")
)


class SafetyStatus(enum.StrEnum):
    """매칭되는 행이 없으면 unknown 이다. unknown 은 저장하지 않는다."""

    ACTIVE = "active"  # 고려 대상
    RETRACTED = "retracted"  # 과거에 active 였고 지금은 고려 대상이 아님
    NONE = "none"  # 확인 결과 없음


_CATEGORY_VALUES = ", ".join(f"'{category.value}'" for category in SafetyCategory)
_ALLERGY_SEVERITY_VALUES = ", ".join(
    f"'{severity.value}'" for severity in sorted(ALLERGY_SEVERITIES)
)


class HealthSafety(Base, UUIDPk, Timestamps):
    """안전·제약 정보. 보호자 입력만 들어온다 — AI 를 거치지 않는다.

    kind      대분류 — allergy / chronic_disease / ...
    label     구체적 대상 — "우유" / "소아 당뇨" / "천식"
    category  알레르기의 분류 목록. kind='allergy' 만 갖고 나머지는 비어 있다.
              NULL 과 '{}' 는 같은 뜻(분류 없음)이다.
    severity  allergy 는 class_0~class_6, 나머지는 mild~anaphylaxis. 어긋나면 CHECK 가 막는다.
    status    active / retracted / none. 행이 없으면 unknown.

    안전 조회는 항상 status='active' 로 필터한다.
    """

    __tablename__ = "health_safety"
    __table_args__ = (
        CheckConstraint(
            f"category <@ ARRAY[{_CATEGORY_VALUES}]::varchar[]",
            name="health_safety_category_values",
        ),
        CheckConstraint(
            "kind = 'allergy' OR coalesce(cardinality(category), 0) = 0",
            name="health_safety_category_allergy_only",
        ),
        CheckConstraint(
            f"severity IS NULL OR (kind = 'allergy') = (severity IN ({_ALLERGY_SEVERITY_VALUES}))",
            name="health_safety_severity_by_kind",
        ),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[SafetyKind] = mapped_column(
        enum_col_py(SafetyKind, name="safety_kind"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(32)), nullable=True, server_default="{}"
    )
    severity: Mapped[SafetySeverity | None] = mapped_column(
        enum_col_py(SafetySeverity, name="safety_severity"),
        nullable=True,
    )
    reactions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    management: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SafetyStatus] = mapped_column(
        enum_col_py(SafetyStatus, name="safety_status"),
        nullable=False,
        server_default="active",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
