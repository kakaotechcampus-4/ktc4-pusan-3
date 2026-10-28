"""푸시 알림 — 받을 기기 (#594).

일정 알림(`schedule.Reminder`)과 복약 알림이 함께 쓴다. 무엇을 언제 울릴지는 각 도메인이,
어디로 보낼지는 여기가 맡는다.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class PushPlatform(enum.StrEnum):
    IOS = "ios"
    ANDROID = "android"


class PushDevice(Base, UUIDPk, Timestamps):
    """알림을 보낼 기기 1대. Reminder 와 분리한다 — 알림 설정과 발송 대상은 다른 생명주기다.

    device_token 유니크 — 로그인·앱 실행·토큰 갱신 시 이 값 기준으로 upsert 한다.
    같은 기기에서 다른 계정으로 로그인하면 같은 행의 parent_id 가 바뀐다.
    🚨 그 upsert 는 parent_id 뿐 아니라 enabled·platform·last_seen_at 도 새 값으로
       덮어써야 한다 — 아니면 이전 보호자의 알림 끔 설정이 새 보호자에게 그대로 남는다.

    device_token 은 다른 토큰(세션·1회용 코드)과 달리 해시가 아니라 원문을 저장한다 —
    APNs/FCM 에 그대로 다시 전달해야 해서 해시로는 발송이 안 된다. 로그에는 남기지 않는다.
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
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
