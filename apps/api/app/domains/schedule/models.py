import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class PushPlatform(enum.StrEnum):
    IOS = "ios"
    ANDROID = "android"


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
    """보호자 개인 알림 설정. 같은 일정이라도 받을지는 보호자마다 다르다.

    parent_id 는 ON DELETE CASCADE 다 — diary_entry.author_parent_id 와 같은 이유로,
    받을 사람 없는 개인 알림 설정은 의미가 없어 작성자가 탈퇴하면 함께 삭제된다.
    """

    __tablename__ = "reminder"
    __table_args__ = (
        Index("ix_reminder_event_id", "event_id"),
        Index("ix_reminder_parent_id", "parent_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushDevice(Base, UUIDPk, Timestamps):
    """알림을 보낼 기기 1대. Reminder 와 분리한다 — 알림 설정과 발송 대상은 다른 생명주기다.

    device_token 유니크 — 로그인·앱 실행·토큰 갱신 시 이 값 기준으로 upsert 한다.
    같은 기기에서 다른 계정으로 로그인하면 같은 행의 parent_id 가 바뀐다.
    """

    __tablename__ = "push_device"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    platform: Mapped[PushPlatform] = mapped_column(
        enum_col_py(PushPlatform, name="push_platform"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiaryEntry(Base, UUIDPk, Timestamps):
    """보호자 개인 기록. 작성자 본인만 조회·수정·삭제한다 (아이 공동 기록이 아니다).

    author_parent_id 는 ON DELETE CASCADE 다 — 작성자 없는 개인 일기는 의미가 없어서,
    다른 공동 기록의 작성자 FK(SET NULL)와 다르게 작성자가 탈퇴하면 이 행도 함께 삭제된다.
    계정은 유지한 채 그 아이와의 연결만 해제되는 경우의 삭제는 서비스 레이어 책임이다.

    (child_id, author_parent_id, date) 유니크 — 옛 calendar 의 "하루 한 행" 불변식을
    작성자 단위로 이어받는다. 같은 보호자가 같은 날 일기를 두 번 쓰면 덮어쓰기다.
    """

    __tablename__ = "diary_entry"
    __table_args__ = (
        UniqueConstraint(
            "child_id", "author_parent_id", "date", name="uq_diary_entry_child_author_date"
        ),
        Index("ix_diary_entry_child_date", "child_id", "date"),
        Index("ix_diary_entry_author_parent_id", "author_parent_id"),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    author_parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class SharedPhoto(Base, UUIDPk, Timestamps):
    """아이 공동 사진. Connected 보호자가 함께 조회·관리한다.

    uploader_parent_id 는 ON DELETE SET NULL 이다 — observation.source_writer 와 같은 패턴으로,
    업로더가 탈퇴해도 사진 자체는 유지하고 참조만 지운다.
    """

    __tablename__ = "shared_photo"
    __table_args__ = (
        Index("ix_shared_photo_child_date", "child_id", "date"),
        Index("ix_shared_photo_uploader_parent_id", "uploader_parent_id"),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    uploader_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
