"""일정 초안 payload 계약. 초안을 만드는 세 경로가 같은 모양을 내게 하는 한 곳.

경로는 한 줄 입력(Memory Agent) / 추천 → 일정(suggestion 도메인) / 기관 공지 OCR
경로마다 조립하는 코드는 따로 갖되 나가는 모양은 여기서 검증한다.

- is_prepared는 없다.
- FastAPI·Starlette를 import 하지 않는다.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

Moment = Annotated[AwareDatetime, PlainSerializer(lambda value: value.isoformat(), return_type=str)]
"""시각은 isoformat 그대로 낸다. 시간대가 없는 값은 받지 않는다.

pydantic 기본 직렬화는 UTC 를 "Z" 로 바꿔 쓴다. DB 에서 읽은 before 는 UTC 로 오므로
기존 payload 표기("+00:00")를 그대로 두려고 고정한다.
"""

# domains/schedule/models.py 의 EventType · EventCategory 와 같은 값.
# core는 domains를 import 하지 않아 값을 다시 적음
EventTypeValue = Literal["core", "episodic"]
EventCategoryValue = Literal["institution", "health", "activity", "etc"]


# 제출 요청(SubmitEventRequest · UpdateEventRequest)이 받는 준비물 상한.
# 초안을 만드는 쪽도 이 안에서만 낸다 — 초안을 그대로 제출했는데 422 가 나면
# 보호자는 무엇을 지워야 하는지 모른다
MAX_DRAFT_ITEMS = 100
MAX_ITEM_NAME = 100


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


# ── 요일 반복 (#738) ─────────────────────────────────────────────
# 반복 일정은 승인할 때 회차마다 일정 행으로 펼쳐진다. 초안 한 장이 묶음 하나를 뜻한다.


class RepeatPayload(DraftBase):
    """반복 규칙. `label` 은 화면이 그대로 그린다 (날짜 계산 · 문구는 서버가 만든다)."""

    weekdays: list[int]  # ISO 요일, 1=월 … 7=일
    until: date  # 마지막 날 (포함)
    label: str


class CreateRepeatEventDraft(CreateEventDraft):
    """반복 일정의 새 초안. POST 본문 최상위의 `repeat` 로 간다 (`event` 와 같은 높이, 문구는 뺀다).

    `event.starts_at` 은 첫 회차의 시각이다. 반복이 아닌 초안에는 `repeat` 키가 없다 —
    항상 null 인 키는 싣지 않는다는 규칙이라 모델을 따로 둔다.
    """

    repeat: RepeatPayload


ClockText = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
"""KST 벽시계 시각 "HH:MM". 묶음은 회차마다 날짜가 달라 하루의 시각만 말한다."""


class SeriesEventBody(DraftBase):
    """묶음 고치기에서 바꾸는 값. 날짜는 없다 — 회차 날짜는 반복 규칙이 정한다."""

    title: str
    starts_time: ClockText | None  # 종일이면 null
    ends_time: ClockText | None
    all_day: bool
    event_type: EventTypeValue
    category: EventCategoryValue


class SeriesBefore(SeriesEventBody):
    """고치기 전 묶음 전체. 화면이 "오전 8:30 → 오전 9:00" 를 그리는 데 쓴다."""

    repeat: RepeatPayload
    items: list[str]


class UpdateSeriesDraft(DraftBase):
    """`PATCH /children/{cid}/event-series/{sid}` 로 간다. 오늘 이후 회차만 고쳐진다."""

    draft_id: str | None
    op: Literal["update_series"]
    series_id: str
    event: SeriesEventBody
    repeat: RepeatPayload
    before: SeriesBefore | None
    items: list[str]  # 준비물 이름. 같은 이름은 회차의 챙김 표시를 지킨다
