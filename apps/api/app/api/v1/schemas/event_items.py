"""준비물 체크 요청 · 응답 — #318 (09 캘린더).

응답의 준비물은 일정 응답의 `items[]` 와 같은 `EventItemOut` 이다.
"""

from pydantic import BaseModel, Field

from app.api.v1.schemas.suggestions import EventItemOut


class EventItemUpdateRequest(BaseModel):
    is_prepared: bool = Field(
        description="최종 상태. 이미 체크된 것을 다시 체크하면 처음 체크한 시각을 그대로 둔다"
    )


class EventItemUpdateResponse(BaseModel):
    item: EventItemOut
