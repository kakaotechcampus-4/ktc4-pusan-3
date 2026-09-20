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


class EventCreatedBy(enum.StrEnum):
    AGENT = "agent"
    CAREGIVER = "caregiver"


class Event(Base, UUIDPk):
    """draft 는 DB 에 쓰지 않는다 (9/17 결정) — status 컬럼 자체를 없앴다.

    #110 이 끝나면 memory agent 는 승인 전 초안을 SSE 로만 내보내고, 이 테이블에는
    보호자가 확인·제출한 행만 쓰게 된다. 지금은 agent 가 아직 이 ORM 을 쓰지 않아
    (자체 in-memory store) 이 불변식을 코드가 강제하지 않는다 — #110 구현 시점에
    실제로 지켜지는지 확인이 필요하다.

    취소는 행을 지운다(hard delete) — 이 테이블에 있다는 것 자체가 "보호자가
    확정함"이라 draft/confirmed/cancelled 를 가르는 상태가 남을 자리가 없다.
    다만 옛 계약서(docs/api/api-interface-v1.html)는 `source_refs` 근거 추적을 위해
    취소해도 삭제하지 않기를 요구했었다 — 이 결정으로 취소된 일정의 근거 추적은
    포기한다. 계약서 갱신은 별도 문서 수정 이슈에서.
    """

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
    created_by: Mapped[EventCreatedBy] = mapped_column(
        enum_col_py(EventCreatedBy, name="event_created_by"), nullable=False
    )
    source_notice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_refs: Mapped[list | None] = mapped_column(JSONB)


class EventItem(Base, Timestamps):
    """#76(동시성 잠금)이 요구한 created_at/updated_at 을 추가했다.

    🚨 Timestamps.updated_at 의 onupdate=func.now() 는 SQLAlchemy ORM/Core update() 가
    나갈 때만 채워진다. raw SQL UPDATE 나 upsert(ON CONFLICT DO UPDATE)로 is_prepared 를
    바꾸면 갱신되지 않는다 — #76 이 이 컬럼을 버전 토큰(compare-and-swap)으로 쓰려면
    이 값으로는 부족하다. 잠금 구현 전에 #76 담당자와 확인할 것.
    """

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
