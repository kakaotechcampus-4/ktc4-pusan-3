import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class DaycareSlot(enum.StrEnum):
    """기관이 정한 끼니 구분 — 그대로 둔다 (Food 포트의 DaycareSlot 과 같은 셋)."""

    LUNCH = "lunch"
    SNACK_AM = "snack_am"
    SNACK_PM = "snack_pm"


class DaycareOrigin(enum.StrEnum):
    OCR = "ocr"  # 급식표 읽기 — 사진 · 센터 배포 엑셀
    CENTER_STANDARD = "center_standard"  # 지역 센터 표준식단. 아이의 실제 급식이 아니다


class DaycareMeal(Base, UUIDPk, Timestamps):
    """어린이집에서 먹는 것 한 끼 — 설계 정본은 docs/agents/food/food_agent_own_table.md §5.

    행을 만드는 것은 급식표 읽기(사진 OCR · 엑셀)뿐이고, Food Agent 는 이미 있는 행을 고치거나
    지울 뿐이다. 급식표에 인쇄된 알레르기 번호는 저장하지 않는다 (10-09).
    오늘 이전 행은 "먹었다" 로 본다 — 상태 칸 없이 날짜가 그 일을 한다. 결석은 행 삭제다.
    """

    __tablename__ = "daycare_meal"
    __table_args__ = (
        # 날짜 범위 조회(child_id, serve_date)도 이 제약의 인덱스가 받는다
        UniqueConstraint(
            "child_id", "serve_date", "meal_slot", name="uq_daycare_meal_child_date_slot"
        ),
    )

    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    # 미래 날짜를 갖는 유일한 식사 테이블 — 급식표는 아직 안 온 날을 싣는다
    serve_date: Mapped[date] = mapped_column(nullable=False)
    meal_slot: Mapped[DaycareSlot] = mapped_column(
        enum_col_py(DaycareSlot, name="daycare_meal_slot"), nullable=False
    )
    # 정규화 메뉴. 대체식이면 교체한다 — 제외 · 대체를 따로 두지 않는다
    menu_keys: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    # 먹은 양은 보통 모른다. 보호자가 말하면 Food 가 고친다
    amount_factor: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("1.00"), server_default="1.00"
    )
    amount_known: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    caregiver_checked: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    origin: Mapped[DaycareOrigin] = mapped_column(
        enum_col_py(DaycareOrigin, name="daycare_meal_origin"),
        nullable=False,
        default=DaycareOrigin.OCR,
        server_default=DaycareOrigin.OCR.value,
    )
    source_notice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notice.id", ondelete="SET NULL")
    )
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
