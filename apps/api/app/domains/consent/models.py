import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk
from app.infra.db.types import enum_col_py


class ConsentScope(enum.StrEnum):
    SERVICE_TERMS = "service_terms"
    PRIVACY_ACCOUNT = "privacy_account"
    CHILD_BASIC = "child_basic"
    CHILD_HEALTH = "child_health"
    QUALITY_IMPROVE = "quality_improve"


class ConsentAction(enum.StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


_ACCOUNT_SCOPE_SQL = "'service_terms','privacy_account'"
"""CHECK 안에서 쓰는 계정 scope 목록.

repository 의 ACCOUNT_SCOPES 와 같은 분류다. 한쪽만 늘어나면 DB 가 막는 것과 코드가
믿는 것이 어긋나므로, 새 scope 를 계정 단위로 추가할 때는 반드시 둘 다 고친다.
"""

_TARGET_MATCHES_SCOPE = (
    f"(scope IN ({_ACCOUNT_SCOPE_SQL}) "
    "AND subject_parent_id IS NOT NULL AND child_id IS NULL) OR "
    f"(scope NOT IN ({_ACCOUNT_SCOPE_SQL}) "
    "AND child_id IS NOT NULL AND subject_parent_id IS NULL)"
)
"""동의 대상은 정확히 하나이고, 그 종류가 scope 와 맞아야 한다.

계정 scope 는 subject_parent_id 를, 아동 scope 는 child_id 를 요구한다. 상호배타와
scope 일치를 한 식으로 쓴다 — 둘을 따로 두면 "둘 다 NULL" 을 한쪽만 막는다.
"""


# append-only — 철회는 UPDATE/DELETE가 아니라 withdrawn 행 추가로 표현한다.
class Consent(Base, UUIDPk):
    """동의 이력 1건 — 노션 정책 정본 §5.

    🚨 행위자(actor)와 동의 대상(subject)은 다른 것이다. 동의한 보호자가 탈퇴해도
       아동 동의는 살아 있어야 하므로 actor 쪽만 SET NULL 로 끊고, 대상 쪽 FK 는
       RESTRICT 로 남겨 증빙 보관 없이 대상이 지워지는 것을 DB 가 막는다.
    """

    __tablename__ = "consent"
    __table_args__ = (
        CheckConstraint(_TARGET_MATCHES_SCOPE, name="ck_consent_target_matches_scope"),
    )

    subject_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="RESTRICT"), nullable=True
    )
    """계정 단위 동의의 대상. 증빙을 옮기기 전에는 이 parent 를 지울 수 없다.

    🚨 NO ACTION 이 아니라 RESTRICT 다. 여기서는 동작이 같지만(둘 다 지연 불가) 스키마에
       남는 값이 다르다 — NO ACTION 은 기본값이라 `ondelete` 를 빠뜨린 것과 구분되지
       않는다. 막는 것이 의도라면 의도가 보여야 한다.
    """

    child_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="RESTRICT"), nullable=True
    )
    """아동 단위 동의의 대상. 같은 이유로 RESTRICT 다."""

    actor_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
    """동의를 누른 보호자. 탈퇴하면 NULL 이 되지만 동의 자체는 무효화되지 않는다."""

    actor_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    """동의 시점의 parent.id 스냅샷. FK 가 아니라서 actor 가 탈퇴해도 남는다.

    🚨 actor_parent_id 와 같은 값으로 시작하지만 같은 컬럼이 아니다. 전자는 "지금도
       있는 계정" 이고 후자는 "그때 누가 눌렀는가" 라는 증빙이다.
    """

    scope: Mapped[ConsentScope] = mapped_column(
        enum_col_py(ConsentScope, name="consent_scope"),
        nullable=False,
    )
    action: Mapped[ConsentAction] = mapped_column(
        enum_col_py(ConsentAction, name="consent_action"),
        nullable=False,
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_version.id", ondelete="RESTRICT"), nullable=False
    )
    """동의 당시 본문. RESTRICT 라 증빙이 남아 있는 동안 그 버전을 지울 수 없다."""

    guardian_attested: Mapped[bool | None]
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConsentRetention(Base, UUIDPk):
    """대상이 삭제된 뒤 남기는 동의 증빙 — 노션 정책 정본 §9.

    운영 consent 와 분리한 이유는 보관 목적이 다르기 때문이다. 이쪽은 일반 서비스
    로직이 조회하지 않고, purge_at 이 지나면 배치가 지운다.

    🚨 참조 컬럼(*_ref)에 FK 를 걸지 않는다. 가리키는 parent·child 가 이미 없는 것이
       정상이다 — FK 를 걸면 증빙을 남기는 것 자체가 불가능해진다.
    """

    __tablename__ = "consent_retention"

    source_consent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_parent_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    child_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope: Mapped[ConsentScope] = mapped_column(
        enum_col_py(ConsentScope, name="consent_scope"),
        nullable=False,
    )
    action: Mapped[ConsentAction] = mapped_column(
        enum_col_py(ConsentAction, name="consent_action"),
        nullable=False,
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_version.id", ondelete="RESTRICT"), nullable=False
    )
    """보관 중에도 동의 본문을 재현할 수 있어야 한다 (정본 §5). 그래서 여기만 FK 다."""

    guardian_attested: Mapped[bool | None]
    acted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """대상을 hard delete 한 시각."""

    purge_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """retained_at + 1년. 지나면 파기한다 — 법정 기간이 아니라 현재 서비스 정책이다."""
