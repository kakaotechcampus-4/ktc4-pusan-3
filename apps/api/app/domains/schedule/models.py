import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class EventType(enum.StrEnum):
    CORE = "core"
    EPISODIC = "episodic"


class EventCategory(enum.StrEnum):
    INSTITUTION = "institution"
    HEALTH = "health"
    ACTIVITY = "activity"
    ETC = "etc"


class EventStatus(enum.StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class EventCreatedBy(enum.StrEnum):
    AGENT = "agent"
    CAREGIVER = "caregiver"


class Event(Base, UUIDPk):
    __tablename__ = "event"

    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        enum_col_py(EventType, name="event_type"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    category: Mapped[EventCategory] = mapped_column(
        enum_col_py(EventCategory, name="event_category"), nullable=False
    )
    status: Mapped[EventStatus] = mapped_column(
        enum_col_py(EventStatus, name="event_status"),
        nullable=False,
        server_default=EventStatus.DRAFT.value,
    )
    created_by: Mapped[EventCreatedBy] = mapped_column(
        enum_col_py(EventCreatedBy, name="event_created_by"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_notice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_refs: Mapped[list | None] = mapped_column(JSONB)


class EventItem(Base):
    __tablename__ = "event_item"

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False
    )
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_prepared: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reminder(Base, UUIDPk):
    __tablename__ = "reminder"

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Calendar(Base, UUIDPk, Timestamps):
    """하루 한 행. 일기 + 사진.

    일정은 event.calendar_id 로 역방향 참조한다 (배열에는 FK 를 걸 수 없어서).
    episodic 일정은 calendar_id 로, core 일정은 starts_at 날짜로 조회한다.
    """

    __tablename__ = "calendar"

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    diary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_urls: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
