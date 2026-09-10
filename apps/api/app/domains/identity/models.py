import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class AuthProvider(str, enum.Enum):
    KAKAO = "kakao"
    APPLE = "apple"
    GOOGLE = "google"
    NAVER = "naver"


class Parent(Base, UUIDPk, Timestamps):
    __tablename__ = "parent"

    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthIdentity(Base, UUIDPk):
    __tablename__ = "auth_identity"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[AuthProvider] = mapped_column(
        enum_col_py(AuthProvider, name="auth_provider"),
        nullable=False,
    )
    provider_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
