"""run 사이에 잠깐 들고 있는 것 — 워커 밖 보관함 (#877 · docs/ops/stateless-v1.md).

api 를 여러 워커로 띄워도 다음 요청이 같은 값을 보게, 프로세스 메모리에 있던 보관함을
이 표로 옮긴다.
1단계는 되묻기 맥락(`pending_reply`) 하나다. 2단계에서 다시 시도 · 사진 run 이 `kind` 를 더한다.

- 🚨 UNLOGGED 다. DB 가 비정상 종료되면 비워지는데, 메모리 보관함과 같은 동작이고 WAL 에 원문이
  남지 않는다. 마이그레이션에 `prefixes=["UNLOGGED"]` 를 손으로 적었다 (autogenerate 는 안 붙인다).
- 🚨 원문 조각이 든다. 백업에서 내용을 빼고(`deploy/scripts/backup.sh`), 삭제 이력도 걸지 않는다
  (`deletion_log.NOT_LOGGED`). 15분 · 24시간 안에 지우는 것이 이 표의 약속이다.
- 탈퇴 · 아이 삭제는 FK CASCADE, 연결 끊기는 `repository.delete_link_holds` 가 지운다.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class HoldKind(enum.StrEnum):
    PENDING_REPLY = "pending_reply"


class RunHold(Base):
    __tablename__ = "run_hold"
    __table_args__ = (
        CheckConstraint("kind IN ('pending_reply')", name="ck_run_hold_kind"),
        Index("ix_run_hold_parent_id", "parent_id"),
        Index("ix_run_hold_child_id", "child_id"),
        Index("ix_run_hold_expires_at", "expires_at"),
        {"prefixes": ["UNLOGGED"]},
    )

    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    """`pending_reply` 는 run_id."""
    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """넣은 시각 + 보관 시간. 넣을 때 정하고, 표시를 풀어도 바꾸지 않는다 (#564)."""
    in_use: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    """답을 받아 이어받기 run 이 쓰는 중이다. 같은 질문에 온 다음 답은 받지 않는다."""
