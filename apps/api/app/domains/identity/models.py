import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class AuthProvider(enum.StrEnum):
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


class AuthSession(Base, UUIDPk):
    """로그인 세션 1건 — 명세 docs/api/auth-kakao-v1.md §5-3.

    클래스 이름만 AuthSession 이다. 테이블은 명세대로 session 이고, 클래스까지 Session 으로
    두면 이 파일에서 SQLAlchemy 의 Session 과 섞인다.

    🚨 토큰 원문은 어디에도 저장하지 않는다. SHA-256 digest(32바이트)만 들고 있다가
       요청이 올 때마다 받은 값을 해시해 대조한다 (§5 · A-18).

    🚨 폐기는 행 삭제다. 세션은 증빙이 아니라서 남은 행은 유출 표면일 뿐이다 —
       consent 가 append-only 인 것과 정반대다. 로그아웃은 그 행 1건, 탈퇴는
       parent_id 로 전부, 만료는 배치로 지운다.

    Timestamps 믹스인을 쓰지 않는다. 세션 행은 갱신되지 않으므로 updated_at 이
    "한 번도 안 변하는 열" 로 남는다. 명세 §5-3 도 created_at 만 둔다.
    """

    __tablename__ = "session"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuthHandoff(Base, UUIDPk):
    """1회용 코드 1건 — 명세 §5-4.

    카카오 왕복이 끝난 뒤 프론트가 세션으로 바꿔갈 때까지만 산다. Redis 가 아니라
    Postgres 인 이유는 §5-4 에 있다 — 동시 존재 행이 사실상 없고, 인프라를 하나 더
    얹을 단계가 아니다.

    🚨 소비는 DELETE … RETURNING 한 문장으로 한다. SELECT 먼저 하고 DELETE 하면
       "조회했는데 그 사이 남이 썼다" 는 틈이 생긴다 (A-04).

    🚨 code 도 bind 도 원문을 담지 않는다. 쿠키가 노출돼도 bind 원문은 얻을 수 없다.

    parent_id 와 provider_user_id 는 정확히 하나만 찬다 —
      기존 회원이면 parent_id, 처음 보는 회원번호면 provider_user_id.
      후자는 signup 트랜잭션이 auth_identity 를 만들 때 쓴다. 둘 다 차면 "기존 회원인데
      새로 만든다" 가 되고, 둘 다 비면 누구의 코드인지 알 수 없다. DB 가 막는다.
    """

    __tablename__ = "auth_handoff"
    __table_args__ = (
        CheckConstraint(
            "(parent_id IS NULL) <> (provider_user_id IS NULL)",
            name="ck_auth_handoff_parent_xor_provider_user",
        ),
    )

    code_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True, index=True)
    bind_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    provider: Mapped[AuthProvider] = mapped_column(
        enum_col_py(AuthProvider, name="auth_provider"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=True
    )
    provider_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
