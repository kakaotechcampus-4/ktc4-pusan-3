"""캘린더 달 · 하루 보기 응답 — #318 (09 캘린더).

일기와 사진은 #115 에서 둘로 나뉘었다 — `my_diary` 는 로그인한 보호자 본인 것만,
`shared_photos` 는 연결된 보호자가 함께 본다. 모양은 #121 제안을 따른다.

🚨 건강 관찰(`observation_health`)은 세지도 싣지도 않는다 — 첫 배포 범위 밖이다.
   보호자가 승인해 저장한 건강 일정(병원 등)은 일정이라 `events` 에 들어간다.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.api.v1.schemas.observations import ObservationOut
from app.api.v1.schemas.suggestions import CalendarEventOut


class CalendarDayOut(BaseModel):
    date: date
    has_event: bool = Field(description="그날과 겹치는 일정이 있다")
    has_my_diary: bool = Field(
        description="로그인한 보호자가 그날 일기를 썼다. 남의 일기는 세지 않는다"
    )
    has_shared_photo: bool = Field(description="그날 공동 사진이 있다")
    observation_count: int = Field(
        description="그날과 기간이 겹치는 기록 수. 하루 보기의 observations 와 같은 기준이다"
    )


class CalendarMonthResponse(BaseModel):
    days: list[CalendarDayOut] = Field(
        description="표시할 것이 하나라도 있는 날만, 날짜순. 빈 날은 싣지 않는다"
    )


class MyDiaryOut(BaseModel):
    content: str
    updated_at: datetime


class SharedPhotoOut(BaseModel):
    id: str
    image_url: str
    created_at: datetime


class CalendarDayResponse(BaseModel):
    my_diary: MyDiaryOut | None = Field(description="로그인한 보호자의 그날 일기. 없으면 null")
    shared_photos: list[SharedPhotoOut] = Field(description="그날 사진. 한 장에 한 항목, 올린 순")
    events: list[CalendarEventOut] = Field(
        description="그날 안에 걸친 일정, 준비물 포함. 그날 00:00 에 끝난 일정은 넣지 않는다"
    )
    observations: list[ObservationOut] = Field(
        description="그날과 기간이 겹치는 기록. 기록 탭 목록과 같은 모양 · 같은 상태 기준이다"
    )
    chat_count: int = Field(
        description=(
            '로그인한 보호자가 그날 나눈 대화 수(#228). "이날 나눈 대화 N개" 로 그리고 '
            "0 이면 그리지 않는다. 남의 대화는 세지 않는다"
        )
    )
