import uuid
from datetime import date

from sqlalchemy import Date, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID  # UUID: child_id용
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk


class Calendar(Base, UUIDPk, Timestamps):
    """하루 한 행. 일기 + 사진.

    일정은 event.calendar_id 로 역방향 참조한다 (배열에는 FK 를 걸 수 없어서).
    episodic 일정은 calendar_id 로, core 일정은 starts_at 날짜로 조회한다.
    """

    __tablename__ = "calendar"

    child_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    diary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_urls: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
