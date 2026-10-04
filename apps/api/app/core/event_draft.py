"""일정 초안 payload 계약. 초안을 만드는 세 경로가 같은 모양을 내게 하는 한 곳.

경로는 한 줄 입력(Memory Agent) / 추천 → 일정(suggestion 도메인) / 기관 공지 OCR
경로마다 조립하는 코드는 따로 갖되 나가는 모양은 여기서 검증한다.

- is_prepared는 없다.
- FastAPI·Starlette를 import 하지 않는다.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, PlainSerializer

Moment = Annotated[AwareDatetime, PlainSerializer(lambda value: value.isoformat(), return_type=str)]
"""시각은 isoformat 그대로 낸다. 시간대가 없는 값은 받지 않는다.

pydantic 기본 직렬화는 UTC 를 "Z" 로 바꿔 쓴다. DB 에서 읽은 before 는 UTC 로 오므로
기존 payload 표기("+00:00")를 그대로 두려고 고정한다.
"""

# domains/schedule/models.py 의 EventType · EventCategory 와 같은 값.
# core는 domains를 import 하지 않아 값을 다시 적음
EventTypeValue = Literal["core", "episodic"]
EventCategoryValue = Literal["institution", "health", "activity", "etc"]


class DraftBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftItemPayload(DraftBase):
    item_id: str | None  # 아직 저장 전인 준비물은 null
    item_name: str


class EventBody(DraftBase):
    title: str
    starts_at: Moment
    ends_at: Moment | None
    all_day: bool
    event_type: EventTypeValue
    category: EventCategoryValue


class EventBefore(EventBody):
    """수정 전 원본. 화면이 "오후 3시 → 오후 5시" 를 그리는 데 쓴다."""

    items: list[DraftItemPayload]


class CreateEventDraft(DraftBase):
    """POST 로 간다. event_id 와 before 는 언제나 null 이라 싣지 않는다."""

    draft_id: str | None
    op: Literal["create"]
    event: EventBody
    items: list[DraftItemPayload]


class UpdateEventDraft(DraftBase):
    """PATCH 로 간다."""

    draft_id: str | None
    op: Literal["update"]
    event_id: str
    event: EventBody
    before: EventBefore | None
    items: list[DraftItemPayload]
