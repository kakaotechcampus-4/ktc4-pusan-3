import uuid
from datetime import date

from sqlalchemy import Date, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk


class Calendar(Base, UUIDPk, Timestamps):
    __tablename__ = "calendar"

    child_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    diary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_urls: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    event_ids: Mapped[list] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
