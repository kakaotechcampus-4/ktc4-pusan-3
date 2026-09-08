import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk


class EventType(str, enum.Enum):
    CORE = "core"
    EPISODIC = "episodic"


class EventCategory(str, enum.Enum):
    INSTITUTION = "institution"
    HEALTH = "health"
    ACTIVITY = "activity"
    ETC = "etc"


class EventStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class EventCreatedBy(str, enum.Enum):
    AGENT = "agent"
    CAREGIVER = "caregiver"


def _enum(py_enum: type[enum.Enum], name: str) -> Enum:
    return Enum(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        create_constraint=True,
    )


class Event(Base, UUIDPk):
    __tablename__ = "event"

    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[EventType] = mapped_column(_enum(EventType, "event_type"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    category: Mapped[EventCategory] = mapped_column(
        _enum(EventCategory, "event_category"), nullable=False
    )
    status: Mapped[EventStatus] = mapped_column(
        _enum(EventStatus, "event_status"), nullable=False, server_default=EventStatus.DRAFT.value
    )
    created_by: Mapped[EventCreatedBy] = mapped_column(
        _enum(EventCreatedBy, "event_created_by"), nullable=False
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
