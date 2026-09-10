import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk
from app.infra.db.types import enum_col_py


class CorrectionVerdict(enum.StrEnum):
    CONFIRM = "confirm"
    ONCE_ONLY = "once_only"
    OUTDATED = "outdated"
    WRONG = "wrong"


class Correction(Base, UUIDPk):
    """append-only. 프로필에 대한 보호자 판정.

    verdict 가 profile_affinity 의 state·strength 를 바꾼다.
      confirm    strength 상향, state 유지
      once_only  한 단계 강등
      outdated   state = archived
      wrong      state = archived, 근거 관찰도 제외

    Timestamps 믹스인을 쓰지 않는다 — 수정되지 않는 테이블이다.
    되돌리기는 새 행을 쌓는다 (outdated 뒤에 confirm).
    """

    __tablename__ = "correction"

    affinity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_affinity.id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[CorrectionVerdict] = mapped_column(
        enum_col_py(CorrectionVerdict, name="correction_verdict"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
