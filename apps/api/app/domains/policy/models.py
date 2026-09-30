from datetime import datetime

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.consent.models import ConsentScope
from app.infra.db.base import Base, UUIDPk
from app.infra.db.types import enum_col_py


class PolicyVersion(Base, UUIDPk):
    """약관·개인정보처리방침 등 정책 본문의 정본. immutable — 등록 후 수정·삭제하지 않는다.

    문구가 바뀌면 기존 row 를 덮어쓰지 않고 scope 는 같고 version 만 다른 새 row 를
    추가한다. 그래서 Timestamps(updated_at 포함)를 쓰지 않고 created_at 만 둔다.
    `consent.policy_version_id` 는 이 row 를 참조해 동의 당시 본문을 재현한다(PR C).
    """

    __tablename__ = "policy_version"
    __table_args__ = (UniqueConstraint("scope", "version", name="uq_policy_version_scope_version"),)

    scope: Mapped[ConsentScope] = mapped_column(
        enum_col_py(ConsentScope, name="consent_scope"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    """체크박스 옆 제목 · 전문의 제목. 본문처럼 그 버전의 글이다 (#168 리뷰).

    코드에 두면 제목을 바꾸는 순간 옛 버전에 동의한 기록에도 새 제목이 소급 적용된다.
    """
    legal_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    """화면에 그대로 보여 주는 근거 조문. 제목과 같은 이유로 버전마다 둔다.

    없으면 보여 주지 않는다.
    """
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    """content 로 만든 정본 HTML — 보호자가 "전문 보기" 에서 보는 그 페이지 (#172, 멘토 #71-4).

    서버가 만든 완성본을 저장하고 화면은 손대지 않고 띄운다. 보여 줄 때마다 새로 만들지 않는다 —
    변환기가 바뀌어도 이미 동의받은 글의 모양이 따라 바뀌면 안 된다. 원고와 함께
    `alembic/policy_texts/<버전>/<scope>.html` 로 커밋하고 마이그레이션이 그대로 넣는다.

    null 이면 정본이 없다 — draft-0 은 "TODO" 자리 표시 글이라 만들지 않았다.
    """
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
