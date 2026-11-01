"""푸시 기기 등록 · 알림 켜고 끄기 요청 · 응답 (#594)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domains.notification.models import PushPlatform


class PushDeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 앞뒤 공백을 자른 뒤 길이를 본다 — 공백만 있는 값이 토큰으로 저장되지 않게 (#605 리뷰)
    token: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
    ] = Field(
        description="FCM 등록 토큰 원문. 웹은 Firebase JS SDK `getToken`, 앱은 셸이 넘겨준 값"
    )
    platform: PushPlatform


class NotificationSettings(BaseModel):
    """설정 탭의 스위치 2개. 알림 시각은 여기가 아니라 일정마다 고른다."""

    model_config = ConfigDict(extra="forbid")

    schedule_enabled: bool = Field(description="일정 알림을 받는다")
    medication_enabled: bool = Field(description="복약 알림을 받는다")
