import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class ParentChildRelation(enum.StrEnum):
    MOTHER = "mother"
    FATHER = "father"
    GRANDPARENT = "grandparent"
    SITTER = "sitter"
    OTHER = "other"


class Gender(enum.StrEnum):
    """아이 성별. 기본값은 "밝히지 않을래요" 다 (루트 CLAUDE.md §2).

    "모름" 이 아니라 "밝히지 않음" 이다 — 보호자는 알고 있고, 우리에게 알려 줄지를 고른다.
    화면 표시 전용이다. Agent 컨텍스트에 넣지 않는다 (10-10 결정).
    """

    MALE = "male"
    FEMALE = "female"
    UNDISCLOSED = "undisclosed"


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
    # default 와 server_default 를 둘 다 둔다. server_default 만 있으면 ORM 이 flush 뒤 이 칸을
    # 모르는 채로 남겨, 비동기 세션에서 읽는 순간 지연 로딩 오류가 난다
    gender: Mapped[Gender] = mapped_column(
        enum_col_py(Gender, name="child_gender"),
        nullable=False,
        default=Gender.UNDISCLOSED,
        server_default=Gender.UNDISCLOSED.value,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parent.id", ondelete="SET NULL")
    )


class ChildGrowthLog(Base, UUIDPk, Timestamps):
    """키 · 몸무게를 잰 날 한 줄. 지표가 아니라 기록이다 (루트 CLAUDE.md §2).

    🚨 증감 · 백분위 · 또래 비교 칸을 만들지 않는다 — 발달 평가로 읽힌다.
    표 이름은 Agent 포트(agents/food/store/ports.py 의 GrowthLogReader)가 이미 쓰는 이름이다.
    공동 기록이라 적은 보호자가 탈퇴해도 남고(created_by SET NULL), 아이가 지워지면 같이 지워진다.
    """

    __tablename__ = "child_growth_log"
    __table_args__ = (
        # 한쪽만 재고 오는 날이 있다. 둘 다 비면 잰 것이 없는 기록이다
        CheckConstraint(
            "height_cm IS NOT NULL OR weight_kg IS NOT NULL", name="child_growth_log_measured"
        ),
        CheckConstraint(
            "height_cm IS NULL OR height_cm > 0", name="child_growth_log_height_positive"
        ),
        CheckConstraint(
            "weight_kg IS NULL OR weight_kg > 0", name="child_growth_log_weight_positive"
        ),
        Index("ix_child_growth_log_child_id_measured_on", "child_id", "measured_on"),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    measured_on: Mapped[date] = mapped_column(nullable=False)
    # float 가 아니라 Numeric — 104.2 가 104.19999 로 바뀌지 않게
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
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
