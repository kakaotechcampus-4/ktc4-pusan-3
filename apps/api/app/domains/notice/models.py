import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class NoticeDocKind(enum.StrEnum):
    """원문이 어떤 문서인가. 분류는 항목이 하고, 여기는 문서의 겉모양만 적는다."""

    DAILY_NOTE = "daily_note"  # 알림장
    NEWSLETTER = "newsletter"  # 가정통신문 · 앱 공지
    MEAL_PLAN = "meal_plan"  # 식단표 — 메뉴 행은 daycare_meal 로 간다
    SCHEDULE = "schedule"  # 월간 일정표 · 교육계획
    MESSAGE = "message"  # 문자 · 알림톡
    OTHER = "other"


class NoticeInputFormat(enum.StrEnum):
    IMAGE = "image"
    PDF = "pdf"
    HWP = "hwp"
    HWPX = "hwpx"
    XLSX = "xlsx"
    TEXT = "text"


class NoticeAudience(enum.StrEnum):
    """이 원문이 누구를 향했는가.

    🚨 알림장은 같은 문장을 반 전체에 "개별 알림장" 으로 보낼 수 있다. 우리 아이에게 온 글이
       우리 아이에 대한 글은 아니다. `child` 가 아니면 관찰로 승격시키는 경로에 넣지 않는다.
    """

    CHILD = "child"
    CLASS = "class"
    INSTITUTION = "institution"
    UNKNOWN = "unknown"


class NoticeRelation(enum.StrEnum):
    CORRECTION = "correction"  # 정정 · 변경 공지
    FOLLOW_UP = "follow_up"  # 안내 → 정산, 1차 → 2차


class NoticeItemHandling(enum.StrEnum):
    """코드가 갈라지는 기준은 이 셋뿐이다.

    "회신 · 비용 · 운영 변경" 같은 내용 이름은 `label` 글자 칸에 둔다 — 공지에 뭐가 나올지 미리
    다 모르고, 이름이 늘어도 처리는 안 늘어난다.
    """

    CALENDAR = "calendar"  # 일정 초안 → 승인 게이트 ㉠. 🚨 여기서 event 를 만들지 않는다
    TODO = "todo"  # 보호자가 할 일 (회신 · 납부 · 준비물)
    INFO = "info"  # 보여 주기만


class NoticeItemTarget(enum.StrEnum):
    CHILD = "child"
    CLASS = "class"
    ALL = "all"
    CONDITIONAL = "conditional"  # 희망자 · 증상이 있는 아이 · 해당자만
    UNKNOWN = "unknown"


class Notice(Base, UUIDPk, Timestamps):
    """기관에서 온 원문 1건 — 알림장 · 가정통신문 · 식단표 · 문자.

    보호자가 확인하고 저장한 뒤에만 이 표에 들어온다 (사진 읽기 중에는 run 에만 있다).

    `child_id` 가 있는 이유 — 회신서가 "반 · 유아명" 단위고, 형제가 받은 공지는 각자의 일이다.
    (child_id, content_hash) 유니크 — 공동 양육자가 같은 공지를 올려도 한 행이다.

    🚨 `raw_text` 는 사람 이름 · 다니는 기관 이름 · 전화번호를 가린 글자만 담는다. 원본 이미지는
       저장하지 않는다 — 이미지에는 다른 아이 얼굴이 있고, 원 이름은 위치를 드러낸다.
    """

    __tablename__ = "notice"
    __table_args__ = (
        UniqueConstraint("child_id", "content_hash", name="uq_notice_child_content_hash"),
        Index("ix_notice_child_received_at", "child_id", "received_at"),
        # 참조하던 공지가 지워지면 related_notice_id 만 NULL 이 된다 — 그래서 "둘 다 있거나 둘 다
        # 없다" 로 걸면 지울 때 이 CHECK 가 막는다
        CheckConstraint(
            "related_notice_id IS NULL OR relation IS NOT NULL",
            name="ck_notice_related_needs_relation",
        ),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    uploader_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL")
    )
    doc_kind: Mapped[NoticeDocKind] = mapped_column(
        enum_col_py(NoticeDocKind, name="notice_doc_kind"), nullable=False
    )
    input_format: Mapped[NoticeInputFormat] = mapped_column(
        enum_col_py(NoticeInputFormat, name="notice_input_format"), nullable=False
    )
    audience_scope: Mapped[NoticeAudience] = mapped_column(
        enum_col_py(NoticeAudience, name="notice_audience_scope"),
        nullable=False,
        server_default=NoticeAudience.UNKNOWN.value,
    )
    # 알림장 · 문자에는 발행일이 없다. 그때 연도는 received_at 에서 빌린다
    issued_on: Mapped[date | None] = mapped_column()
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    title: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    related_notice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notice.id", ondelete="SET NULL")
    )
    relation: Mapped[NoticeRelation | None] = mapped_column(
        enum_col_py(NoticeRelation, name="notice_relation")
    )
    # 어떤 모델 · 프롬프트로 읽었는지. 프롬프트를 고친 뒤 다시 뽑을 행을 고른다
    extractor_version: Mapped[str | None] = mapped_column(Text)


class NoticeItem(Base, UUIDPk, Timestamps):
    """원문에서 뽑은 항목 1건. 공지 한 장에 여러 건이 섞여 오는 것이 보통이다.

    아이 관찰과 급식은 여기 없다. 그 둘은 자기 표(`observation_*` · `daycare_meal`)에 들어가
    `source_notice_id` 로 원문을 가리킨다. 이 표는 보호자가 할 일 · 알 일만 담는다.

    🚨 날짜는 모델이 계산하지 않는다. 모델은 원문 표현만 옮기고, 연도 채우기 · 요일 검산은
       `app/rules/notice_dates.py` 가 한다 (CLAUDE.md §2 · §3).
    🚨 `calendar` 행은 초안 재료일 뿐이다. 캘린더에 쓰는 문은 `POST /children/{cid}/events` 하나다
       (승인 게이트 ㉠). 이 행으로 만든 일정은 `event.source_notice_item_id` 가 1:1 로 가리킨다.

    확인이 필요하다는 표시(`needs_review`)는 칸으로 두지 않는다. 확인하지 않은 항목은 저장 API 가
    받지 않아서, 이 표에 들어온 행은 전부 보호자가 확인한 것이다.
    """

    __tablename__ = "notice_item"
    __table_args__ = (
        Index("ix_notice_item_notice_id", "notice_id"),
        Index(
            "ix_notice_item_open_due",
            "due_at",
            postgresql_where=text("due_at IS NOT NULL AND done_at IS NULL"),
        ),
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at >= starts_at",
            name="ck_notice_item_range",
        ),
    )

    # 공지를 지우면 항목도 지운다. 공지 id 가 바뀌는 일은 없어야 해서 바꾸려 하면 막는다
    notice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notice.id", ondelete="CASCADE", onupdate="RESTRICT"),
        nullable=False,
    )
    handling: Mapped[NoticeItemHandling] = mapped_column(
        enum_col_py(NoticeItemHandling, name="notice_item_handling"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)  # 화면에 보일 내용 이름
    title: Mapped[str] = mapped_column(Text, nullable=False)
    target_scope: Mapped[NoticeItemTarget] = mapped_column(
        enum_col_py(NoticeItemTarget, name="notice_item_target"),
        nullable=False,
        server_default=NoticeItemTarget.UNKNOWN.value,
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 마감은 시각까지 있을 때도("14시까지") 날짜뿐일 때도("내일") 있다 — event.all_day 와 같은 구분
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 금액 · 장소 · 회신 수단처럼 보여 주기만 하는 값. 코드가 갈라지거나 정렬하는 값은 칸으로 둔다
    details: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    supplies: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    source_quote: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # 원문에서 그대로 옮긴 근거 문장
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
