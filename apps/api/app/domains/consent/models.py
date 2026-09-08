import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk


class ConsentScope(str, enum.Enum):
    SERVICE_TERMS = "service_terms"
    PRIVACY_ACCOUNT = "privacy_account"
    CHILD_BASIC = "child_basic"
    CHILD_HEALTH = "child_health"
    QUALITY_IMPROVE = "quality_improve"


class ConsentAction(str, enum.Enum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


# append-only — 철회는 UPDATE/DELETE가 아니라 withdrawn 행 추가로 표현한다.
class Consent(Base, UUIDPk):
    __tablename__ = "consent"
    __table_args__ = (
        CheckConstraint(
            "(scope IN ('service_terms','privacy_account') AND child_id IS NULL) OR "
            "(scope NOT IN ('service_terms','privacy_account') AND child_id IS NOT NULL)",
            name="ck_consent_scope_child_id",
        ),
    )

    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parent.id"), nullable=False)
    child_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("child.id"))
    scope: Mapped[ConsentScope] = mapped_column(
        Enum(
            ConsentScope,
            name="consent_scope",
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    action: Mapped[ConsentAction] = mapped_column(
        Enum(
            ConsentAction,
            name="consent_action",
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    guardian_attested: Mapped[bool | None]
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
