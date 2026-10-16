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
    """기기 로그인 1건 — refresh 를 맡는다. 명세 docs/api/auth-kakao-v1.md §5-3.

    클래스 이름만 AuthSession 이다. 테이블은 명세대로 session 이고, 클래스까지 Session 으로
    두면 이 파일에서 SQLAlchemy 의 Session 과 섞인다.

    access 는 이 행이 아니라 SessionAccess 에 둔다 (#466). 한 행에 access 칸을 하나만 두면
    탭 두 개가 번갈아 refresh 할 때 서로의 access 를 덮어써 401 이 반복된다.

    🚨 토큰 원문은 어디에도 저장하지 않는다. SHA-256 digest(32바이트)만 들고 있다가
       요청이 올 때마다 받은 값을 해시해 대조한다 (§5 · A-18).

    🚨 폐기는 행 삭제다. 세션은 증빙이 아니라서 남은 행은 유출 표면일 뿐이다 —
       consent 가 append-only 인 것과 정반대다. 로그아웃은 그 행 1건, 탈퇴는
       parent_id 로 전부, 만료는 배치로 지운다. 딸린 access 는 CASCADE 로 같이 사라진다.

    Timestamps 믹스인을 쓰지 않는다. 행이 바뀌는 건 refresh 회전뿐이고 그 시각은
    rotated_at 이 따로 든다 — 재사용 유예 판정에 쓰는 값이라 updated_at 과 섞지 않는다.
    """

    __tablename__ = "session"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 지금 유효한 refresh
    refresh_hash: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, unique=True, index=True
    )
    # 직전 refresh — 재사용 탐지용. 한 번도 회전하지 않았으면 비어 있다
    prev_refresh_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, unique=True, index=True
    )
    # 마지막 회전 시각 — 재사용 유예 판정용
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 유휴 만료. 회전마다 연장하되 absolute_expires_at 을 넘지 않는다
    refresh_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # 절대 만료. 로그인 시각 기준이고 연장하지 않는다
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SessionAccess(Base):
    """access 토큰 1건 — 일반 API 와 SSE 가 Bearer 로 싣는 값 (#466).

    한 세션에 여러 행이 있을 수 있다. refresh 할 때마다 한 행이 생기고, 회전 전에 받은
    access 도 만료 전까지는 그대로 통한다 (탭 여러 개).

    🚨 원문이 아니라 SHA-256 digest 가 PK 다 (A-18). 매 요청이 이 값으로 찾는다.
    """

    __tablename__ = "session_access"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
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
