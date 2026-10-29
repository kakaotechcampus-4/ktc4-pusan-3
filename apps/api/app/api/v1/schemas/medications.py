"""복약 — `POST /children/{cid}/medications` 의 요청 · 응답 (#577).

🚨 프론트 apps/web/src/lib/api/types.ts 의 SubmitMedicationBody · SubmitMedicationResponse ·
   MedicationCourse · MedicationDose 와 같은 모양이어야 한다. 서버가 칸을 더하지 않는다.
   초안 모양은 app/core/medication_draft.py 의 `MedicationDraft.to_payload()` 이고, 이 요청은 그
   `schedule` · `doses` 를 보호자가 확인한 뒤 되돌려 보낸 것이다. 화면 전용 칸(`draft_id` ·
   `notice_times` · `missing` · `before`)과 서버가 계산하는 `scheduled_time` 은 받지 않는다(400).

필드의 뜻은 `Field(description=...)` 에 적는다 — Swagger 가 그대로 보여 준다. 길이 · 기간 같은
내용 검증은 서비스(`domains/medication/service.py`)가 하고 틀린 칸 이름으로 400 을 낸다.
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.medication_draft import ClockTime
from app.core.symptom_codes import validate_symptom_codes
from app.domains.medication.models import (
    MedicationForm,
    MedicationSource,
    MedicationStorage,
    TimingAnchor,
)

_REQUEST_EXAMPLE = {
    "source": "utterance",
    "schedule": {
        "title": "아목시실린",
        "dosage": "5ml",
        "indication_text": "중이염",
        "note": None,
        "storage": None,
        "med_form": "liquid",
        "symptom_codes": [],
        "starts_on": "2026-10-27",
        "ends_on": "2026-10-29",
    },
    "doses": [
        {"timing_anchor": "breakfast", "offset_minutes": 30, "fixed_time": None},
        {"timing_anchor": "lunch", "offset_minutes": 30, "fixed_time": None},
        {"timing_anchor": "dinner", "offset_minutes": 30, "fixed_time": None},
    ],
}

HHMM = Annotated[ClockTime, Field(examples=["08:30"])]


class MedicationScheduleIn(BaseModel):
    """복약 코스의 내용 — 약 **1종**의 복용 계획. 약이 3종이면 코스 3건이다."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        description=(
            "약 이름. 1~200자(한글 1자 = 1), 공백만이면 400. **받은 그대로 저장한다** — 앞뒤 "
            "공백을 "
            "서버가 떼지 않는다. 같은 아이 · 같은 이름 · 같은 기간의 진행 중인 코스가 있으면 409"
        ),
        examples=["아목시실린"],
    )
    dosage: str | None = Field(
        default=None,
        description="1회 용량 — '5ml' · '1정'. 200자 이하. 빈 문자열은 null 로 저장한다",
        examples=["5ml"],
    )
    indication_text: str | None = Field(
        default=None,
        description="복약 이유 — 보호자가 말한 문장. 500자 이하. 빈 문자열은 null 로 저장한다",
        examples=["중이염"],
    )
    note: str | None = Field(
        default=None,
        description="보호자 특이사항(투약의뢰서 칸). 1000자 이하. 빈 문자열은 null 로 저장한다",
    )
    storage: MedicationStorage | None = Field(
        default=None, description="보관 방법. room(실온) · fridge(냉장). 모르면 null"
    )
    med_form: MedicationForm | None = Field(
        default=None,
        description="제형. liquid · powder · tablet · ointment · drops. 모르면 null",
    )
    symptom_codes: list[str] = Field(
        default_factory=list,
        description=(
            "복약 이유를 증상 사전(`reference/symptom_codes.yaml`)으로 맞춘 코드. 화면은 고치지 "
            "않고 "
            "초안의 값을 그대로 보낸다. 사전에 없는 코드 · 빈 코드 · 겹친 코드는 400"
        ),
    )
    starts_on: date = Field(
        description=(
            "복용 시작일 `YYYY-MM-DD`(한국 날짜). 과거여도 되지만 **지난 날짜의 회차는 만들지 "
            "않는다**"
        ),
        examples=["2026-10-27"],
    )
    ends_on: date = Field(
        description=(
            "복용 마지막 날 `YYYY-MM-DD`. 시작일보다 빠르거나 기간이 **90일**(끝 − 시작 + 1)을 "
            "넘으면 "
            "400, 이미 지난 날이면 400. 무기한 코스는 없다"
        ),
        examples=["2026-10-29"],
    )

    @field_validator("symptom_codes")
    @classmethod
    def _check_codes(cls, codes: list[str]) -> list[str]:
        if any(not code.strip() for code in codes):
            raise ValueError("증상 코드는 비어 있을 수 없다")
        if len(set(codes)) != len(codes):
            raise ValueError("증상 코드가 겹친다")
        return validate_symptom_codes(codes)


class MedicationDoseIn(BaseModel):
    """하루 시각 패턴 한 칸 — 날짜가 없다.

    서버가 `starts_on`~`ends_on` 의 날마다 펼쳐 회차를 만든다. 알림 시각은 `timing_anchor` 의
    기준 시각 + `offset_minutes` 로 서버가 계산한다(`scheduled_time` 은 받지 않는다).
    예: breakfast(08:00) + 30분 = 08:30.
    """

    model_config = ConfigDict(extra="forbid")

    timing_anchor: TimingAnchor = Field(
        description=(
            "기준점. `wake_up`(07:30) · `breakfast`(08:00) · `lunch`(12:00) · `dinner`(19:00) · "
            "`bedtime`(20:30) 은 기준 시각이 정해져 있고, `fixed` 는 기준 없이 `fixed_time` 을 "
            "직접 쓴다"
        ),
        examples=["breakfast"],
    )
    offset_minutes: int = Field(
        description=(
            "기준점에서 떨어진 분. 식후 30분은 `30`, 식전 30분은 `-30`. **-120 ~ 180**, "
            "`fixed` 면 반드시 0. 범위를 벗어나면 400(`doses`)"
        ),
        examples=[30],
    )
    fixed_time: HHMM | None = Field(
        default=None,
        description=(
            "`fixed` 일 때만 쓰는 직접 시각 `HH:MM`(분 단위, 초는 400). `fixed` 가 아니면 null. "
            "`fixed` 인데 비면 400(`doses`)"
        ),
    )


class SubmitMedicationRequest(BaseModel):
    """승인 게이트 ㉡ — 보호자가 복약 카드에서 확인한 **최종 상태 전체**.

    LLM 이 이 요청을 만들지 않는다. Agent 는 초안까지만 만들고, 이 요청이 오기 전에는
    코스도 회차도 저장되지 않는다.
    """

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_REQUEST_EXAMPLE]})

    schedule: MedicationScheduleIn = Field(description="코스의 내용")
    doses: list[MedicationDoseIn] = Field(
        description=(
            "하루 시각 패턴. **1~24개**, 같은 알림 시각이 둘이면 400(`anchor` 가 달라도 계산한 "
            "시각이 "
            "같으면 같은 시각이다). 0개이거나 25개 이상이어도 400(`doses`)"
        )
    )
    source: MedicationSource = Field(
        description="코스의 출처. utterance(대화에서 말한 것) · prescription_ocr(처방전 사진)"
    )


class MedicationDoseOut(BaseModel):
    """회차 1번 — **체크 칸이 곧 복용 기록이다**(따로 로그가 없다)."""

    id: UUID = Field(description="회차 id. 체크 · 해제에 쓴다")
    dose_date: date = Field(description="먹일 날 `YYYY-MM-DD`(한국 날짜)")
    scheduled_time: ClockTime = Field(
        description="알림 시각 `HH:MM`. 서버가 기준점 + 오프셋으로 계산한 값이다"
    )
    checked_at: datetime | None = Field(
        description="보호자가 체크한 시각. null 이면 아직 안 먹인 것(미체크)이다"
    )
    checkable: bool = Field(
        description=(
            "지금 체크 · 해제할 수 있는가. 서버가 오늘과 비교해 정한다 — 화면이 날짜를 계산하지 "
            "않는다. "
            "응답에는 오늘까지의 회차만 실려서 지금은 항상 true 다"
        )
    )


class MedicationCourseOut(BaseModel):
    """등록된 코스 1건 — 약 **1종**의 복용 계획."""

    id: UUID = Field(description="코스 id")
    title: str = Field(description="약 이름. 보낸 그대로다")
    dosage: str | None = Field(description="1회 용량. 없으면 null")
    starts_on: date = Field(description="복용 시작일 `YYYY-MM-DD`")
    ends_on: date = Field(description="복용 마지막 날 `YYYY-MM-DD`")
    notice_times: list[str] = Field(
        description="하루에 알림이 울릴 시각 `HH:MM`, 오름차순. 예: `['08:30','12:30','19:30']`"
    )
    doses: list[MedicationDoseOut] = Field(
        description=(
            "**오늘까지**의 회차, (날짜, 시각) 순. 아직 오지 않은 날의 회차는 싣지 않는다 — "
            "내일부터 "
            "먹는 약을 오늘 등록하면 빈 목록이다"
        )
    )


class SubmitMedicationResponse(BaseModel):
    """201 — 등록한 코스."""

    course: MedicationCourseOut = Field(description="등록된 코스")


class MedicationsResponse(BaseModel):
    """200 — 진행 중인 코스. 멈춘 · 끝난 · 시작 전 코스는 싣지 않는다."""

    today: date = Field(
        description=(
            "서버의 오늘 `YYYY-MM-DD`(한국 날짜). 화면은 오늘을 따로 구하지 않고 이 값을 쓴다 — "
            "'지금까지 온 날' 의 기준이다"
        )
    )
    courses: list[MedicationCourseOut] = Field(
        description=(
            "진행 중인 코스 — `active` 이고 시작일 ≤ 오늘 ≤ 끝일. 시작일 순, 같으면 만든 순. "
            "없으면 빈 목록이다"
        )
    )
