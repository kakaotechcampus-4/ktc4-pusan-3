import enum
import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class ParentChildRelation(enum.StrEnum):
    MOTHER = "mother"
    FATHER = "father"
    GRANDPARENT = "grandparent"
    SITTER = "sitter"
    OTHER = "other"


class ParentChild(Base, UUIDPk):
    __tablename__ = "parent_child"
    # 보호자당 아이 1명. 아이 등록(#92)과 초대 수락(#198)이 이 위반을
    # 409 child_already_exists 로 바꾼다.
    # 아이 삭제는 hard delete(purge_child)라 이 행도 CASCADE 로 함께 지워진다 (invite-v1.md §7-05).
    # child.deleted_at 만 채우면 이 행이 남아 그 보호자는 재등록·수락이 영영 막힌다.
    __table_args__ = (UniqueConstraint("parent_id", name="uq_parent_child_parent_id"),)

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[ParentChildRelation] = mapped_column(
        enum_col_py(ParentChildRelation, name="parent_child_relation"), nullable=False
    )
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

    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parent.id", ondelete="SET NULL")
    )
    # 원문 코드는 저장하지 않는다 — 정규화한 값의 SHA-256 (session · auth_handoff 와 같은 원칙).
    code_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parent.id", ondelete="SET NULL"))
