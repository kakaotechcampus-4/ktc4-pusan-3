"""푸시 기기 등록 · 알림 켜고 끄기 요청 · 응답 (#594)."""

from pydantic import BaseModel, ConfigDict, Field

from app.domains.notification.models import PushPlatform


class PushDeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        min_length=1,
        max_length=4096,
        description="FCM 등록 토큰 원문. 웹은 Firebase JS SDK `getToken`, 앱은 셸이 넘겨준 값",
    )
    platform: PushPlatform


class NotificationSettings(BaseModel):
    """설정 탭의 스위치 2개. 알림 시각은 여기가 아니라 일정마다 고른다."""

    model_config = ConfigDict(extra="forbid")

    schedule_enabled: bool = Field(description="일정 알림을 받는다")
    medication_enabled: bool = Field(description="복약 알림을 받는다")
