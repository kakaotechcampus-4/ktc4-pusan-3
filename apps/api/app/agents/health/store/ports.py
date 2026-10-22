"""Health가 읽는 데이터의 계약.

생일 · 동의 포트와 `SafetyLookupError` 는 `common/ports.py` 에 있다. health_safety 를 읽는
`HealthSafetyReader` 와 `HealthSafetyEntry` 는 Health 가 따로 둔다 — Food · Activity · Growth 와
읽는 범위가 달라서(active 행의 kind 전부, 관리 칸까지) 합치면 한쪽 규칙이 다른 쪽에 새어 들어간다.

| 포트 | 연결 대상 |
| ChildProfileReader | Child_Profile — birth_date 만 |
| ConsentReader | consent(scope=child_health) 최신 행 |
| HealthSafetyReader | health_safety — active 행, kind 전부. 응급 카드 · 진료 준비 · 반복 안내 |
| HealthMemoryReader | observation_health — 증상 · 체온 · 먹인 약, observation_food — 최근 식사 |
| MedicationReader | medication_schedule · medication_dose — 진행 중 코스 · 회차 · 체크 |
| MedicationWriter | medication_schedule — 확인된 중단 하나. Agent 가 쓰는 유일한 것 |
| ChildBasicReader | child — 별명 · 생일. 응급 카드 |

복약 포트의 칸 이름은 `medication_schedule` · `medication_dose` 스키마와 같다
(docs/agents/health/health_agent_own_table.md §7).
장소 포트는 그 tool 을 만드는 이슈가 더한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal, Protocol, Self
from uuid import UUID

from app.agents.common.ports import (
    ChildProfileReader,
    ConsentReader,
    SafetyLookupError,  # noqa: F401 — context.py · inmemory.py 가 여기서 가져간다
)
from app.core.medication_draft import TimingAnchor, scheduled_time_of

# health_safety 의 kind · status 와 같은 값. 어긋나면 어댑터 테스트가 잡는다
SafetyKind = Literal["allergy", "chronic_disease", "behavioral", "environmental", "other_medical"]
SafetyStatus = Literal["active", "retracted", "none"]


@dataclass(frozen=True)
class HealthSafetyEntry:
    kind: SafetyKind
    label: str  # 보호자가 적은 이름 원문
    status: SafetyStatus
    severity: str | None = None
    reactions: tuple[str, ...] = ()
    management: str | None = None


class HealthSafetyReader(Protocol):
    async def health_safety(self, *, child_id: UUID) -> list[HealthSafetyEntry]:
        """active 행만, kind 는 전부. 못 읽으면 `SafetyLookupError` — 빈 목록이 아니다."""
        ...


@dataclass(frozen=True)
class HealthObservation:
    """observation_health 한 행 — 증상 · 먹인 약 기록. 읽기 전용이다.

    한 행 = 한 사건이다. 먹인 약 행은 `symptoms == ()` 이고 `medication_given` 이 있다 —
    증상 코드가 없어서 반복 판정 · 에피소드에는 세지 않는다.
    """

    id: UUID
    observed_from: date  # observed_range 시작
    observed_to: date  # 포함 (range 끝 - 1일). 하루짜리 기록은 observed_from 과 같다
    observed_time: datetime | None  # 몇 시인지 말했을 때만. DB 에서 온 값은 UTC 오프셋일 수 있다
    symptoms: tuple[str, ...]  # 보호자가 쓴 말 원문. 코드로 바꾸는 건 tools/symptoms.py
    severity: str | None = None
    action_taken: str | None = None
    # 아래는 보호자가 말한 그대로다 — 단위 환산 · 보정 없음 (DB: numeric(3,1) · timestamptz)
    temperature: Decimal | None = None
    measured_at: datetime | None = None  # 잰 시각. DB 에서 온 값은 UTC 오프셋일 수 있다
    measure_site: str | None = None  # ear · forehead · armpit · oral · rectal
    medication_given: str | None = None  # 보호자가 말한 약 이름 원문. 이 값이 있으면 약 행이다
    # 같은 말에서 함께 기록된 증상 행. 코드가 채운다 — "때문에" 가 아니라 묶음 표시다
    linked_symptom_id: UUID | None = None


@dataclass(frozen=True)
class MealRecord:
    """observation_food 한 행 — 최근에 먹은 것. 영양 숫자는 두지 않는다."""

    id: UUID
    observed_on: date
    subject: str
    amount_text: str | None  # 보호자가 쓴 양 원문 ("반 그릇")


class HealthMemoryReader(Protocol):
    async def health_observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[HealthObservation]:
        """[date_from, date_to] 의 증상 · 먹인 약 기록. 정정으로 내린 기록(wrong)은 빠져 있다."""
        ...

    async def meals(self, *, child_id: UUID, date_from: date, date_to: date) -> list[MealRecord]:
        """[date_from, date_to] 의 식사 기록. Food 와 같은 상태(active · stand_alone)만 읽는다."""
        ...


@dataclass(frozen=True)
class DoseSlot:
    """하루 시각 패턴 한 칸 (날짜 없음). 초안의 `DoseDraft` 와 같은 칸이다."""

    timing_anchor: TimingAnchor
    offset_minutes: int
    fixed_time: time | None
    scheduled_time: time

    @classmethod
    def of(
        cls, anchor: TimingAnchor, offset_minutes: int = 0, *, fixed_time: time | None = None
    ) -> Self:
        scheduled = scheduled_time_of(anchor, offset_minutes, fixed_time)
        return cls(anchor, offset_minutes, fixed_time, scheduled)


@dataclass(frozen=True)
class MedicationCourse:
    id: UUID
    title: str  # 보호자 발화 원문
    dosage: str | None
    indication_text: str | None
    storage: Literal["room", "fridge"] | None
    med_form: Literal["liquid", "powder", "tablet", "ointment", "drops"] | None
    starts_on: date
    ends_on: date  # 필수 — 무기한 코스는 없다
    status: Literal["active", "stopped"]  # 끝남은 status 가 아니라 ends_on 으로 본다
    note: str | None  # 투약의뢰서 특이사항(보호자 입력)
    # 오늘 이후 회차 행에서 시각이 겹치지 않게 모은 것(남은 회차가 없으면 마지막 날의 행에서).
    # 수정 초안의 before · 서식의 투약 시각에 쓴다. 어댑터는 이 규칙으로 회차 행에서 만든다
    pattern: tuple[DoseSlot, ...]
    # 저장된 값(`medication_schedule.symptom_codes`). 수정 초안은 사전으로 다시 계산하지 않고
    # 이 값을 싣는다 — 제출 API 는 받은 값을 그대로 저장해서, 다시 계산하면 옛 코스의 값이 덮인다
    symptom_codes: tuple[str, ...] = ()
    # 보호자가 멈춘 시각. status == "stopped" 일 때만 뜻이 있다 — DB 어댑터가 `medication_schedule.
    # updated_at` 에서 채운다(`MedicationWriter.stop_course` 가 그 칸에 중단 시각을 쓴다. 어댑터는
    # 복약 연결 이슈 몫). 멈춘 코스도 앞으로의 회차 행은 남아 있어서(soft delete) 이 시각 뒤 회차는
    # 계획이 아니다. None 이면 모른다 — 모르는 값으로 회차를 지우지 않는다
    stopped_at: datetime | None = None


@dataclass(frozen=True)
class MedicationDose:
    """회차 1행. 체크는 보호자가 화면에서 한다 — Agent 는 읽기만 한다."""

    id: UUID
    schedule_id: UUID
    dose_date: date  # 아이 타임존(KST) 날짜
    # DB 칸은 nullable 이지만(#546) CHECK 가 NULL 이 되는 입력을 막는다. NULL 이 오면 어댑터 버그다
    scheduled_time: time
    checked_at: datetime | None  # None = 체크 안 됨. "안 먹였다" 는 뜻이 아니다


class MedicationReader(Protocol):
    async def active_courses(self, *, child_id: UUID, today: date) -> list[MedicationCourse]:
        """status='active' AND starts_on <= today <= ends_on. 끝난 코스는 ends_on 으로 빠진다.

        BE 의 `in_progress(today)`(app/domains/medication/models.py)와 같은 조건이다. 어댑터는 그
        함수를 쓰고, `today` 는 KST 날짜(`context.today`)를 그대로 넘긴다 — DB 의 CURRENT_DATE 는
        UTC 다.
        """
        ...

    async def courses_overlapping(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[MedicationCourse]:
        """status 와 상관없이 기간이 [date_from, date_to] 와 겹치는 코스 — 끝났거나 멈춘 것도."""
        ...

    async def doses(
        self, *, schedule_ids: Sequence[UUID], date_from: date, date_to: date
    ) -> list[MedicationDose]:
        """dose_date 가 [date_from, date_to] 인 회차 행. (dose_date, scheduled_time) 순."""
        ...


class MedicationWriter(Protocol):
    async def stop_course(
        self, *, schedule_id: UUID, stopped_at: datetime
    ) -> Literal["stopped", "already", "missing"]:
        """status='stopped' · updated_at=stopped_at. 이미 멈춘 코스는 시각을 안 바꾸고 "already"."""
        ...


@dataclass(frozen=True)
class ChildBasic:
    nickname: str
    birth_date: date


class ChildBasicReader(Protocol):
    async def basic(self, *, child_id: UUID) -> ChildBasic: ...


@dataclass(frozen=True)
class HealthPorts:
    """아이 하나 몫의 읽기 포트 묶음. `medication_writer` 만 쓰기다 — 확인된 중단 하나."""

    profile: ChildProfileReader
    consent: ConsentReader
    safety: HealthSafetyReader
    memory: HealthMemoryReader
    medication: MedicationReader
    medication_writer: MedicationWriter
    child: ChildBasicReader
