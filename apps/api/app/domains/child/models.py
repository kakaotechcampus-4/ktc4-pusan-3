import enum
import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk


class ParentChildRelation(str, enum.Enum):
    MOTHER = "mother"
    FATHER = "father"
    GRANDPARENT = "grandparent"
    SITTER = "sitter"
    OTHER = "other"


def _relation_enum() -> Enum:
    return Enum(
        ParentChildRelation,
        name="parent_child_relation",
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        create_constraint=True,
    )


class ParentChild(Base, UUIDPk):
    __tablename__ = "parent_child"
    __table_args__ = (UniqueConstraint("parent_id", "child_id"),)

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[ParentChildRelation] = mapped_column(_relation_enum(), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Child(Base, UUIDPk, Timestamps):
    __tablename__ = "child"

    nickname: Mapped[str] = mapped_column(Text, nullable=False)
    owner_parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parent.id"), nullable=False)
    birth_date: Mapped[date] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parent.id", ondelete="SET NULL")
    )


class Invite(Base, UUIDPk):
    __tablename__ = "invite"
    __table_args__ = (UniqueConstraint("token"),)

    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parent.id", ondelete="SET NULL")
    )
    token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"))
