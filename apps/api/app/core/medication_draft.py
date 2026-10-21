"""복약 초안 payload 계약. 초안을 만드는 Agent 와 저장하는 제출 API 가 같은 모양을 쓰는 한 곳.

일정 초안(`event_draft.py`)과 같은 자리 · 같은 규칙이다. Agent 는 이 모델로 초안을 만들고,
제출 API 는 자기 요청 모델(`SubmitEventBody` 처럼)을 따로 두되 `DoseDraft` · `ANCHOR_TIMES` ·
`OFFSET_MIN/MAX` · `TimingAnchor` 를 가져다 쓴다.

- `doses`는 하루 시각 패턴(날짜 없음). 제출 때 백엔드가 `starts_on` ~ `ends_on` 의 날마다
  펼쳐 회차 행(`medication_dose`)을 만든다.
- 검증은 DB CHECK 를 그대로 옮긴다.
- FastAPI · Starlette · `domains` 를 import 하지 않는다.

🚨 `ANCHOR_TIMES` 는 `medication_dose.scheduled_time` 생성 컬럼과 같은 값이어야 한다. 대조 테스트는
테이블을 만드는 쪽에 둔다.
"""

from collections.abc import Mapping
from datetime import date, time
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, PlainSerializer, field_validator, model_validator

from app.core.event_draft import DraftBase

TimingAnchor = Literal["fixed", "wake_up", "breakfast", "lunch", "dinner", "bedtime"]

# 기준 시각. "식후" 처럼 시점으로 말한 것을 알림 시각으로 바꿀 때 쓴다 (기준점 + 오프셋)
ANCHOR_TIMES: Mapping[str, time] = {
    "wake_up": time(7, 30),
    "breakfast": time(8, 0),
    "lunch": time(12, 0),
    "dinner": time(19, 0),
    "bedtime": time(20, 30),
}
# medication_dose.offset_minutes CHECK 와 동일
OFFSET_MIN, OFFSET_MAX = -120, 180

# medication_schedule.storage · med_form CHECK 와 같은 값. core 는 domains 를 import 하지 않는다
StorageValue = Literal["room", "fridge"]
MedFormValue = Literal["liquid", "powder", "tablet", "ointment", "drops"]

# 비면 제출할 수 없는 칸. 비어 있으면 `missing` 에 실린다 (NOT NULL 컬럼)
REQUIRED_SCHEDULE_FIELDS = ("title", "starts_on", "ends_on")


def _whole_minute(value: time) -> time:
    if value.second or value.microsecond:
        raise ValueError("시각은 분 단위다")
    return value


ClockTime = Annotated[
    time,
    AfterValidator(_whole_minute),
    PlainSerializer(lambda value: value.strftime("%H:%M"), return_type=str),
]
"""시각은 "HH:MM" 으로 낸다. 화면이 그대로 보여 주는 값이고 `notice_times` 와 같은 표기다."""


def scheduled_time_of(anchor: TimingAnchor, offset_minutes: int, fixed_time: time | None) -> time:
    """회차의 알림 시각. DB 의 scheduled_time 생성 컬럼과 같은 계산이다."""
    if anchor == "fixed":
        if fixed_time is None:
            raise ValueError("fixed 는 fixed_time 이 있어야 한다")
        return fixed_time
    base = ANCHOR_TIMES[anchor]
    minutes = (base.hour * 60 + base.minute + offset_minutes) % (24 * 60)
    return time(minutes // 60, minutes % 60)


class DoseDraft(DraftBase):
    """하루 시각 패턴 한 칸."""

    timing_anchor: TimingAnchor
    offset_minutes: Annotated[int, Field(ge=OFFSET_MIN, le=OFFSET_MAX)]  # fixed 면 0
    fixed_time: ClockTime | None  # anchor 가 fixed 일 때만
    scheduled_time: ClockTime  # scheduled_time_of(...) 와 같아야 한다

    @model_validator(mode="after")
    def _check_columns(self) -> Self:
        if (self.timing_anchor == "fixed") != (self.fixed_time is not None):
            raise ValueError("fixed 와 fixed_time 은 함께여야 한다")
        if self.timing_anchor == "fixed" and self.offset_minutes != 0:
            raise ValueError("fixed 의 offset_minutes 는 0 이어야 한다")
        expected = scheduled_time_of(self.timing_anchor, self.offset_minutes, self.fixed_time)
        if self.scheduled_time != expected:
            raise ValueError(f"scheduled_time 은 {expected:%H:%M} 이어야 한다")
        return self


class ScheduleDraft(DraftBase):
    """코스 한 건. 값이 없으면 None 이고, 필수 칸이 비면 `missing` 에 실린다."""

    title: str | None
    dosage: str | None
    indication_text: str | None
    storage: StorageValue | None  # 보호자가 카드에서 적는 선택 칸 — Agent는 None
    med_form: MedFormValue | None  # 〃
    note: str | None  # 투약의뢰서 특이사항 — 〃
    # 복약 이유(`indication_text`)를 증상 사전(`reference/symptom_codes.yaml`)으로 맞춘 코드.
    # Agent 가 사전으로 만들어 싣고 제출 API 는 받은 값을 그대로 저장한다 — 모델이 고르지 않는다
    symptom_codes: list[str] = Field(default_factory=list)
    starts_on: date | None
    ends_on: date | None  # 제출 때는 필수(무기한 코스 방지)

    @field_validator("symptom_codes")
    @classmethod
    def _check_codes(cls, codes: list[str]) -> list[str]:
        if any(not code.strip() for code in codes):
            raise ValueError("증상 코드는 비어 있을 수 없다")
        if len(set(codes)) != len(codes):
            raise ValueError("증상 코드가 겹친다")
        return codes

    @model_validator(mode="after")
    def _check_span(self) -> Self:
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on 은 starts_on 이상이어야 한다")
        return self


class MedicationSnapshot(DraftBase):
    """수정 전 원본. 화면이 "19:30 → 20:00" 을 그리는 데 쓴다."""

    schedule: ScheduleDraft
    doses: list[DoseDraft]


class MedicationDraft(DraftBase):
    draft_id: str  # 화면이 초안을 가리키고 제출 요청에 되돌려 보내는 키
    op: Literal["create", "update"]
    source: str | None  # "utterance" · (처방전 경로가 생기면) "prescription_ocr"
    schedule: ScheduleDraft
    doses: Annotated[list[DoseDraft], Field(min_length=1)]  # 같은 시각 둘 금지
    notice_times: list[str]  # 실제로 울릴 시각 "HH:MM" 정렬
    missing: list[str]  # 비어 있는 필수 칸. 비지 않으면 화면이 제출을 잠근다
    schedule_id: str | None = None  # update 에만
    before: MedicationSnapshot | None = None  # update 에만

    @model_validator(mode="after")
    def _check_draft(self) -> Self:
        times = [dose.scheduled_time for dose in self.doses]
        if len(set(times)) != len(times):
            raise ValueError("같은 시각의 dose 가 둘 이상이다")
        if self.notice_times != sorted(f"{moment:%H:%M}" for moment in times):
            raise ValueError("notice_times 는 doses 의 scheduled_time 과 같아야 한다")
        empty = {name for name in REQUIRED_SCHEDULE_FIELDS if getattr(self.schedule, name) is None}
        if len(self.missing) != len(set(self.missing)) or set(self.missing) != empty:
            raise ValueError(
                "missing 은 비어 있는 필수 칸(title · starts_on · ends_on)과 같아야 한다"
            )
        if self.op == "create" and (self.schedule_id is not None or self.before is not None):
            raise ValueError("create 초안에는 schedule_id · before 가 없다")
        if self.op == "update" and (self.schedule_id is None or self.before is None):
            raise ValueError("update 초안에는 schedule_id 와 before 가 있다")
        return self

    def to_payload(self) -> dict[str, Any]:
        """화면으로 나가는 JSON. create 는 항상 null인 schedule_id · before 를 싣지 않는다."""
        payload = self.model_dump(mode="json")
        if self.op == "create":
            del payload["schedule_id"], payload["before"]
        return payload
