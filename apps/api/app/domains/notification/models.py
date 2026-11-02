"""푸시 알림 — 받을 기기 (#594).

일정 알림(`schedule.Reminder`)과 복약 알림이 함께 쓴다. 무엇을 언제 울릴지는 각 도메인이,
어디로 보낼지는 여기가 맡는다.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class PushPlatform(enum.StrEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"  # 브라우저 — Firebase JS SDK 가 준 FCM 토큰. 테스트는 웹으로 한다


class PushDevice(Base, UUIDPk, Timestamps):
    """알림을 보낼 기기 1대 = 로그인 1건(`session`)의 FCM 토큰. Reminder 와 분리한다.

    🚨 로그인에 묶는다(`session_id` CASCADE). 로그아웃 · 세션 만료 · 탈퇴로 세션이 지워지면 기기도
       함께 지워진다 — 공용 기기에서 로그아웃한 뒤에도 앞 보호자의 알림이 울리는 일이 없다.
       화면이 로그아웃할 때 따로 지우라고 부르지 않아도 된다. 세션은 refresh 회전에도 같은 행이라
       로그인이 이어지는 동안은 기기도 그대로다.
    - 로그인 하나에 기기 하나(`session_id` 유니크). 토큰이 바뀌면 같은 행을 새 토큰으로 덮는다.
    - 토큰도 유니크다. 같은 기기에서 다른 계정으로 로그인하면 앞 로그인의 행을 지우고 새로 넣는다.
    - parent_id 는 세션의 보호자와 같다. 발송이 "이 보호자들의 기기" 를 세션을 거치지 않고 찾는다.
    - 켜고 끄기는 기기가 아니라 보호자 단위다(`NotificationSetting`). 기기마다 끄는 칸은 없다.

    device_token 은 다른 토큰(세션·1회용 코드)과 달리 해시가 아니라 원문을 저장한다 —
    FCM 에 그대로 다시 전달해야 해서 해시로는 발송이 안 된다. 로그에는 남기지 않는다.
    """

    __tablename__ = "push_device"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    device_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    platform: Mapped[PushPlatform] = mapped_column(
        enum_col_py(PushPlatform, name="push_platform"), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotificationSetting(Base):
    """보호자의 알림 켜고 끄기 — 설정 탭의 스위치 2개. 시각은 여기서 고르지 않는다 (일정별 `lead`).

    행이 없으면 아직 한 번도 바꾸지 않은 것이고, 기본값(`repository.DEFAULT_SETTING`)으로 본다.
    기본값을 칸 기본값(server_default)으로 두지 않는 이유 — 기본값이 바뀌면 스위치를 만진 적
    없는 보호자 모두에게 새 값이 적용돼야 하는데, 행에 박혀 있으면 마이그레이션으로 고쳐야 한다.
    """

    __tablename__ = "notification_setting"

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), primary_key=True
    )
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    medication_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
