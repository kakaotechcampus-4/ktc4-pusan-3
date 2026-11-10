"""run 채널의 표 미러 — 다른 워커에 붙은 화면(SSE)이 읽는다 (#877 4단계 · docs/ops/stateless-v1.md).

run 을 돌리는 워커는 메모리 채널(`api/runs/registry.py`)을 그대로 쓰고, 내보낸 이벤트를 순서대로
여기에도 쓴다. 다른 워커는 이 표를 짧은 주기로 읽는다.

- 🚨 UNLOGGED 다. DB 가 비정상 종료되면 비워진다 — 메모리 채널이 재시작에 사라지던 것과 같다.
- 🚨 실패 이벤트에는 보호자가 적은 문장 원문이 있다. 닫힌 뒤 5분 · 끝나지 않으면 10분 뒤
  `hold_purge` 가 지운다(이벤트는 CASCADE). 백업에서 내용을 빼고 삭제 이력도 걸지 않는다.
- 보호자를 지우면 FK CASCADE 로 함께 지워진다.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class RunChannelRow(Base):
    __tablename__ = "run_channel"
    __table_args__ = (
        Index("ix_run_channel_parent_id", "parent_id"),
        Index("ix_run_channel_expires_at", "expires_at"),
        {"prefixes": ["UNLOGGED"]},
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    ended_with: Mapped[str | None] = mapped_column(Text)
    """나간 끝 신호(failed · done). 같은 키 재생 판정이 본다."""
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    """더 올 이벤트가 없다. 다른 워커의 SSE 가 여기까지 읽고 끝낸다."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunEvent(Base):
    __tablename__ = "run_event"
    __table_args__ = ({"prefixes": ["UNLOGGED"]},)

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("run_channel.run_id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    """메모리 채널의 events 순번(0부터)."""
    name: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
