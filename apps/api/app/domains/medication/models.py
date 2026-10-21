"""복약 코스와 회차 (#537). 결정과 근거는 docs/medication/medication-tables-v1.md.

이 표에는 보호자가 제출한 코스만 들어간다 — 초안은 payload 로 나가고 제출 API 가 INSERT 한다
(게이트 ㉡). Agent 가 직접 쓰는 것은 확인받은 중단(`status='stopped'`) 하나뿐이다.
"""

import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class MedicationStatus(enum.StrEnum):
    """끝남(completed)은 없다 — `ends_on` 으로 계산한다. 아래 `in_progress` 참고."""

    ACTIVE = "active"
    STOPPED = "stopped"


class MedicationCreatedBy(enum.StrEnum):
    AGENT = "agent"
    CAREGIVER = "caregiver"


class MedicationSource(enum.StrEnum):
    UTTERANCE = "utterance"
    PRESCRIPTION_OCR = "prescription_ocr"


class MedicationStorage(enum.StrEnum):
    ROOM = "room"
    FRIDGE = "fridge"


class MedicationForm(enum.StrEnum):
    LIQUID = "liquid"
    POWDER = "powder"
    TABLET = "tablet"
    OINTMENT = "ointment"
    DROPS = "drops"


class TimingAnchor(enum.StrEnum):
    FIXED = "fixed"
    WAKE_UP = "wake_up"
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    BEDTIME = "bedtime"


# 🚨 기준 시각은 여기와 app/core/medication_draft.py 의 ANCHOR_TIMES 두 곳에 있다(#544).
#    바꾸면 STORED 생성 컬럼이라 테이블을 다시 쓰는 마이그레이션이 필요하다.
SCHEDULED_TIME_SQL = """CASE timing_anchor
    WHEN 'fixed' THEN fixed_time
    WHEN 'wake_up' THEN TIME '07:30' + make_interval(mins => offset_minutes)
    WHEN 'breakfast' THEN TIME '08:00' + make_interval(mins => offset_minutes)
    WHEN 'lunch' THEN TIME '12:00' + make_interval(mins => offset_minutes)
    WHEN 'dinner' THEN TIME '17:00' + make_interval(mins => offset_minutes)
    WHEN 'bedtime' THEN TIME '20:30' + make_interval(mins => offset_minutes)
END"""


class MedicationSchedule(Base, UUIDPk, Timestamps):
    """복약 코스 1건. 끝나는 날이 필수다 — 회차 행을 기간만큼 미리 만들기 때문이다.

    🚨 중단은 `updated_at` 이 중단 시각이다. `onupdate` 는 ORM update 때만 채워지니
       raw SQL 로 중단하면 `updated_at = now()` 를 직접 쓴다.
    """

    __tablename__ = "medication_schedule"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="medication_schedule_period"),
        Index("medication_schedule_child_idx", "child_id", "status"),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    dosage: Mapped[str | None] = mapped_column(Text)
    indication_text: Mapped[str | None] = mapped_column(Text)
    symptom_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    storage: Mapped[MedicationStorage | None] = mapped_column(
        enum_col_py(MedicationStorage, name="medication_storage")
    )
    med_form: Mapped[MedicationForm | None] = mapped_column(
        enum_col_py(MedicationForm, name="medication_form")
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[MedicationStatus] = mapped_column(
        enum_col_py(MedicationStatus, name="medication_status"),
        nullable=False,
        server_default=MedicationStatus.ACTIVE.value,
    )
    # 보호자 특이사항. 초안 카드에서 보호자가 적는 선택 칸이고 Agent 는 비워 둔다
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[MedicationCreatedBy] = mapped_column(
        enum_col_py(MedicationCreatedBy, name="medication_created_by"), nullable=False
    )
    source: Mapped[MedicationSource] = mapped_column(
        enum_col_py(MedicationSource, name="medication_source"),
        nullable=False,
        server_default=MedicationSource.UTTERANCE.value,
    )
    # 처방전 경로(prescription_draft)가 생기면 FK(ON DELETE SET NULL)를 단다
    source_draft_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class MedicationDose(Base, UUIDPk):
    """회차 1번 = 1행. 3일 × 하루 3번이면 9행이고, 체크 칸이 곧 복용 기록이다.

    created_at · updated_at 이 없다 — 제출 때 한꺼번에 만들어지고 바뀌는 건 체크뿐인데,
    그 시각은 `checked_at` 에 남는다.
    """

    __tablename__ = "medication_dose"
    __table_args__ = (
        CheckConstraint(
            "(timing_anchor = 'fixed') = (fixed_time IS NOT NULL)",
            name="medication_dose_fixed_time",
        ),
        CheckConstraint(
            "timing_anchor <> 'fixed' OR offset_minutes = 0", name="medication_dose_fixed_offset"
        ),
        # 기준 시각 05:30 ~ 23:30 안에 머물러 time + interval 이 자정을 넘지 않는다
        CheckConstraint("offset_minutes BETWEEN -120 AND 180", name="medication_dose_offset_range"),
        UniqueConstraint("schedule_id", "dose_date", "scheduled_time"),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("medication_schedule.id", ondelete="CASCADE"),
        nullable=False,
    )
    dose_date: Mapped[date] = mapped_column(Date, nullable=False)  # KST 날짜
    timing_anchor: Mapped[TimingAnchor] = mapped_column(
        enum_col_py(TimingAnchor, name="medication_timing_anchor"), nullable=False
    )
    offset_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    fixed_time: Mapped[time | None] = mapped_column(Time)
    # 이슈 DDL 대로 NOT NULL 을 달지 않았다. 위 CHECK 와 enum CHECK 가 NULL 이 되는 입력을 막는다
    scheduled_time: Mapped[time | None] = mapped_column(
        Time, Computed(SCHEDULED_TIME_SQL, persisted=True)
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def in_progress(today: date) -> ColumnElement[bool]:
    """진행 중인 코스. 끝남을 저장하지 않으니 판정은 이 조건 한 곳에서 한다.

    `today` 는 KST 오늘을 받는다 — DB 의 CURRENT_DATE 는 UTC 라 자정 부근에 하루 어긋난다.
    """
    return and_(
        MedicationSchedule.status == MedicationStatus.ACTIVE,
        MedicationSchedule.ends_on >= today,
    )
