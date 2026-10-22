"""Health 라이브 eval 의 가짜 세계 — 아이 · 동의 · 안전 정보. 전부 지어낸 값이다.

`test_expected_health.md` §0 의 픽스처 이름(C48 · C48-noconsent …)을 그대로 쓴다. 기준 시각은 명세
픽스처 기본인 수요일 14:00 이고(`DAY`), 야간 케이스는 같은 날 23:00 이다(`NIGHT`).

복약 코스는 날짜를 오늘에 맞춰 만든다(`CourseFactory`). 복용 체크는 보호자가 화면에서 하는 값이라
세계가 미리 넣어 둔다 — Agent 는 읽기만 한다. 체온 · 먹인 약은 `observations` 에, 식사는 `meals` 에
둔다 — 보호자가 Memory 로 남긴 기록이다. 아직 세계에 못 넣은 데이터(위치 …)는 `pending` 에 적는다 —
그 데이터를 읽는 tool 을 만드는 이슈가 이 표에서 채운다. 결과에는 참고(note)로 남아, 판정이 왜 GAP
인지 보인다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.health.context import HealthContext
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
    in_memory_ports,
)
from app.agents.health.store.ports import (
    DoseSlot,
    HealthObservation,
    HealthSafetyEntry,
    MealRecord,
    MedicationCourse,
    MedicationDose,
)

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID("00000000-0000-7000-8000-000000000048")
DAY = datetime(2026, 10, 14, 14, 0, tzinfo=KST)  # 수요일 14:00
NIGHT = DAY.replace(hour=23)

_PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")
_ASTHMA = HealthSafetyEntry("chronic_disease", "천식", "active")

CourseFactory = Callable[[date], tuple[MedicationCourse, list[MedicationDose]]]


def antibiotic(today: date) -> tuple[MedicationCourse, list[MedicationDose]]:
    """항생제 — 어제 · 오늘 · 내일 3일, 하루 세 번 식후(08:30 · 12:30 · 19:30), 용량 "5ml".

    어제는 세 회차 모두 체크했고 오늘은 08:30 만 체크했다(지금 14:00). 값은 전부 지어낸 것이다.
    """
    yesterday = today - timedelta(days=1)
    return course_with_doses(
        "항생제",
        (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30)),
        yesterday,
        today + timedelta(days=1),
        dosage="5ml",
        checked={(yesterday, time(8, 30)), (yesterday, time(12, 30)), (yesterday, time(19, 30))}
        | {(today, time(8, 30))},
    )


def syrup(today: date) -> tuple[MedicationCourse, list[MedicationDose]]:
    """시럽 — 오늘부터 3일, 자기 전. 항생제와 이름이 겹치지 않는다."""
    return course_with_doses(
        "시럽", (DoseSlot.of("bedtime"),), today, today + timedelta(days=2), dosage="3ml"
    )


def fever_reducer(today: date) -> tuple[MedicationCourse, list[MedicationDose]]:
    """해열제 — 어제 · 오늘 · 내일 3일, 하루 세 번 고정 시각(09:00 · 15:00 · 21:00), 용량 "4ml".

    최근 48시간 안에서 어제 21:00 · 오늘 09:00 두 회차만 체크했다. 어제 09:00 · 15:00 은 체크 안
    됨이다 — "안 먹였다" 가 아니라서 열 타임라인에 나오면 안 된다. 항생제와 시각이 겹치지 않아 다음
    예정이 어느 코스의 회차인지 갈린다(오늘 15:00). 값은 전부 지어낸 것이다.
    """
    yesterday = today - timedelta(days=1)
    return course_with_doses(
        "해열제",
        tuple(DoseSlot.of("fixed", fixed_time=time(hour)) for hour in (9, 15, 21)),
        yesterday,
        today + timedelta(days=1),
        dosage="4ml",
        checked={(yesterday, time(21)), (today, time(9))},
    )


def _record(days_ago: int, symptom: str, serial: int) -> HealthObservation:
    """`DAY` 의 `days_ago` 일 전에 관찰한 증상 기록 한 건 (하루짜리 · 시각 없음)."""
    day = (DAY - timedelta(days=days_ago)).date()
    return HealthObservation(
        id=UUID(int=0x480000 + serial),
        observed_from=day,
        observed_to=day,
        observed_time=None,
        symptoms=(symptom,),
    )


# T25 — "이번 주에 열 나는 게 세 번째야". 기록된 날은 3일이다. 같은 날 한 번 더 적힌 것(serial 4)은
# 날 수를 늘리지 않는다 — 기록 수로 세거나 발화의 "세 번째" 를 믿으면 여기서 어긋난다
_FEVER_THREE_DAYS = (
    _record(5, "열나", 1),
    _record(2, "38도", 2),
    _record(0, "열이 나요", 3),
    _record(0, "미열", 4),
)
# 기침 사흘 내리
_COUGH_THREE_DAYS = tuple(_record(days_ago, "기침", 10 + days_ago) for days_ago in (2, 1, 0))


def _at(days_ago: int, hour: int, minute: int = 0) -> datetime:
    """`DAY` 의 `days_ago` 일 전 그 시각 (KST)."""
    return (DAY - timedelta(days=days_ago)).replace(hour=hour, minute=minute)


def _reading(
    serial: int, at: datetime, temperature: str, site: str | None = None
) -> HealthObservation:
    """잰 체온 한 건 — 증상 행에 얹힌다(체온만 따로 두는 행은 없다). 숫자는 말한 그대로다."""
    return HealthObservation(
        id=UUID(int=0x48E000 + serial),
        observed_from=at.date(),
        observed_to=at.date(),
        observed_time=None,
        symptoms=("열나",),
        temperature=Decimal(temperature),
        measured_at=at,
        measure_site=site,
    )


# T22 열 타임라인 — 최근 48시간(10/12 14:00 이후) 체온 4건. 두 번째 기록과 같은 말로 보호자가
# 먹인 약을 남겼다("새벽 3시에 39도라 좌약 넣었어" 꼴) — 약 행은 그 증상 행에 이어진다
# (코드가 채운 연결)
_FEVER_READINGS = (
    _reading(1, _at(2, 22), "38.4", "ear"),
    _reading(2, _at(1, 3), "39.0", "ear"),
    _reading(3, _at(1, 13), "37.6", "armpit"),
    _reading(4, _at(0, 7), "38.1"),
)
_GIVEN_LINKED = HealthObservation(
    id=UUID(int=0x48E0F1),
    observed_from=_at(1, 3).date(),
    observed_to=_at(1, 3).date(),
    observed_time=_at(1, 3, 10),  # 약 행의 시각은 보호자가 말한 먹인 시각이다
    symptoms=(),
    medication_given="좌약",
    linked_symptom_id=_FEVER_READINGS[1].id,
)
# T22-C2 — 2개월 아이의 오늘 아침 체온 하나. 응급 안내가 타임라인보다 먼저다
_INFANT_READING = _reading(20, _at(0, 6), "38.2")
# T24-norecord — 14일보다 오래된 기록 하나뿐. 기간 밖이라 "기록 없음" 이어야 한다
_OLD_COUGH = _record(20, "기침", 30)


def _meal(days_ago: int, subject: str, amount: str, serial: int) -> MealRecord:
    return MealRecord(
        id=UUID(int=0x48F000 + serial),
        observed_on=(DAY - timedelta(days=days_ago)).date(),
        subject=subject,
        amount_text=amount,
    )


# observation_food — 최근 3일 식사 셋 + 3일 밖 하나. 진료 준비 · 검진 맥락은 3일만 읽는다
_MEALS = (
    _meal(5, "호박죽", "한 그릇", 1),
    _meal(2, "밥", "조금", 2),
    _meal(1, "바나나", "1개", 3),
    _meal(0, "죽", "반 그릇", 4),
)


@dataclass(frozen=True)
class WorldSpec:
    months: int = 48
    consent: bool = True
    safety: tuple[HealthSafetyEntry, ...] = ()
    safety_fails: bool = False
    # observation_health — 증상 기록(체온이 얹힌 것 포함) · 보호자가 기록한 먹인 약 행
    observations: tuple[HealthObservation, ...] = ()
    meals: tuple[MealRecord, ...] = ()  # observation_food — 최근 식사
    courses: tuple[CourseFactory, ...] = ()  # 진행 중 복약 — 날짜는 오늘에 맞춘다
    nickname: str = "별이"
    # 아직 세계에 못 넣은 데이터. 그 tool 이슈가 채운다 (결과에 note 로 남는다)
    pending: str = ""


WORLDS: dict[str, WorldSpec] = {
    "C48": WorldSpec(observations=_FEVER_THREE_DAYS, meals=_MEALS, courses=(antibiotic,)),
    "C48-2med": WorldSpec(courses=(antibiotic, syrup)),
    "C48-nomed": WorldSpec(),
    # T19 응급 카드 — C48 에 알레르기 · 만성질환을 더했다. 카드는 등록된 값을 옮기는 게 전부다
    "C48-card": WorldSpec(safety=(_PEANUT, _ASTHMA), courses=(antibiotic,)),
    # 항생제는 C48 의 코스 그대로다(3일치 회차 · 어제 3회 · 오늘 08:30 체크)
    "C48-fever": WorldSpec(
        observations=(*_FEVER_READINGS, _GIVEN_LINKED), courses=(antibiotic, fever_reducer)
    ),
    "C48-norecord": WorldSpec(observations=(_OLD_COUGH,)),
    "C48-draft": WorldSpec(pending="처방전 경로 — 묶음 밖"),
    "C48-nolocation": WorldSpec(pending="위치 · 장소 포트 — Health-7"),
    "C48-apifail": WorldSpec(pending="위치 · 장소 포트 — Health-7"),
    # 읽으면 안 된다는 걸 보려고 알레르기를 둔다 — 동의가 없으면 읽기 횟수가 0 이어야 한다
    "C48-noconsent": WorldSpec(consent=False, safety=(_PEANUT,)),
    "C48-safetyfail": WorldSpec(
        safety_fails=True, observations=_COUGH_THREE_DAYS, courses=(antibiotic,)
    ),
    "C48-chronic": WorldSpec(
        safety=(HealthSafetyEntry("chronic_disease", "만성 면역력 저하증", "active"),),
        observations=_COUGH_THREE_DAYS,
    ),
    "C48-chronic-unknown": WorldSpec(
        safety=(HealthSafetyEntry("chronic_disease", "테스트 희귀질환", "active"),),
        observations=_COUGH_THREE_DAYS,
    ),
    "C48-allergyonly": WorldSpec(safety=(_PEANUT,), observations=_COUGH_THREE_DAYS),
    **{f"C{months}": WorldSpec(months=months) for months in (20, 23, 24, 35, 36, 71, 72)},
    "C2": WorldSpec(months=2, observations=(_INFANT_READING,)),
}


@dataclass(frozen=True)
class World:
    name: str
    spec: WorldSpec
    context: HealthContext
    safety: InMemoryHealthSafety  # 읽은 횟수(calls)를 판정이 본다
    medication: InMemoryMedicationStore  # 쓴 것(writes · stopped)을 판정이 본다
    courses: tuple[MedicationCourse, ...]  # 세계가 넣어 둔 진행 중 코스 (id 로 대조한다)
    today: date
    doses: tuple[MedicationDose, ...] = ()  # 그 코스들의 회차 행 — 체크 수 · 마지막 체크를 대조한다


def _birth(months: int, today: date) -> date:
    """오늘 기준으로 정확히 `months` 개월인 아이의 생일(일은 오늘과 같다)."""
    total = today.year * 12 + (today.month - 1) - months
    return date(total // 12, total % 12 + 1, today.day)


def build_world(name: str, *, now: datetime = DAY, run_id: str = "live") -> World:
    spec = WORLDS[name]
    today = now.astimezone(KST).date()
    safety = InMemoryHealthSafety(spec.safety, fail=spec.safety_fails)
    seeds = [factory(today) for factory in spec.courses]
    medication = InMemoryMedicationStore(
        [c for c, _ in seeds], [d for _, rows in seeds for d in rows]
    )
    context = HealthContext(
        child_id=CHILD,
        run_id=run_id,
        now=now,
        timezone=KST,
        ports=in_memory_ports(
            CHILD,
            _birth(spec.months, today),
            consent=spec.consent,
            safety=safety,
            memory=InMemoryHealthMemory(spec.observations, spec.meals),
            medication=medication,
            nickname=spec.nickname,
        ),
    )
    return World(
        name=name,
        spec=spec,
        context=context,
        safety=safety,
        medication=medication,
        courses=tuple(c for c, _ in seeds),
        today=today,
        doses=tuple(d for _, rows in seeds for d in rows),
    )
