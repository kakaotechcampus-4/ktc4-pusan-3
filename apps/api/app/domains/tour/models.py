"""화면 가이드(화면 투어)를 본 기록 (#696 · `docs/web/screen-tour-v1.md` §2)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class TourSeen(Base):
    """보호자 1명이 가이드 1개를 봤다. 행이 없으면 아직 안 본 것이다.

    - 아이가 아니라 **계정** 단위다. 같은 아이를 함께 보는 보호자도 각자 센다.
    - `tour_key` 는 enum 이 아니라 text 다. 가이드 목록은 프론트(`lib/tour/types.ts` 의
      `TOUR_KEYS`)가 정하고 화면이 늘 때마다 늘어난다 — 허용 값은 API 스키마(`TourKey`)가 막고,
      표는 마이그레이션 없이 받는다.
    - `seen_at` 은 처음 본 시각이다. ? 로 다시 봐도 바꾸지 않는다.
    - parent_id 는 ON DELETE CASCADE — 탈퇴하면 함께 지운다.
    """

    __tablename__ = "tour_seen"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), primary_key=True
    )
    tour_key: Mapped[str] = mapped_column(Text, primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
