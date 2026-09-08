from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.memory.enums import enum_col
from app.infra.db.base import Base, UUIDPk


class Correction(Base, UUIDPk):
    """append-only. Timestamps 믹스인을 쓰지 않는다."""

    __tablename__ = "correction"

    target_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    verdict: Mapped[str] = mapped_column(
        enum_col("confirm", "once_only", "outdated", "wrong", name="correction_verdict"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
