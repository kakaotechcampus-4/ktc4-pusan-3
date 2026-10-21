"""Health 라이브 eval 의 가짜 세계 — 아이 · 동의 · 안전 정보. 전부 지어낸 값이다.

`test_expected_health.md` §0 의 픽스처 이름(C48 · C48-noconsent …)을 그대로 쓴다. 기준 시각은 명세
픽스처 기본인 수요일 14:00 이고(`DAY`), 야간 케이스는 같은 날 23:00 이다(`NIGHT`).

복약 코스는 날짜를 오늘에 맞춰 만든다(`CourseFactory`). 복용 체크는 보호자가 화면에서 하는 값이라
세계가 미리 넣어 둔다 — Agent 는 읽기만 한다. 아직 세계에 못 넣은 데이터(체온 · 위치 …)는
`pending` 에 적는다 — 그 데이터를 읽는 tool 을 만드는 이슈가 이 표에서 채운다. 결과에는
참고(note)로 남아, 판정이 왜 GAP 인지 보인다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
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
    MedicationCourse,
    MedicationDose,
)

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID("00000000-0000-7000-8000-000000000048")
DAY = datetime(2026, 10, 14, 14, 0, tzinfo=KST)  # 수요일 14:00
NIGHT = DAY.replace(hour=23)

_PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")
_ASTHMA = HealthSafetyEntry("chronic_disease", "천식", "active")
_FEVER_PENDING = "체온 기록 · 진료 준비 — Health-6"

CourseFactory = Callable[[date], tuple[MedicationCourse, list[MedicationDose]]]


def antibiotic(today: date) -> tuple[MedicationCourse, list[MedicationDose]]:
    """항생제 — 어제 · 오늘 · 내일 3일, 하루 세 번 식후(08:30 · 12:30 · 17:30), 용량 "5ml".

    어제는 세 회차 모두 체크했고 오늘은 08:30 만 체크했다(지금 14:00). 값은 전부 지어낸 것이다.
    """
    yesterday = today - timedelta(days=1)
    return course_with_doses(
        "항생제",
        (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30)),
        yesterday,
        today + timedelta(days=1),
        dosage="5ml",
        checked={(yesterday, time(8, 30)), (yesterday, time(12, 30)), (yesterday, time(17, 30))}
        | {(today, time(8, 30))},
    )


def syrup(today: date) -> tuple[MedicationCourse, list[MedicationDose]]:
    """시럽 — 오늘부터 3일, 자기 전. 항생제와 이름이 겹치지 않는다."""
    return course_with_doses(
        "시럽", (DoseSlot.of("bedtime"),), today, today + timedelta(days=2), dosage="3ml"
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


@dataclass(frozen=True)
class WorldSpec:
    months: int = 48
    consent: bool = True
    safety: tuple[HealthSafetyEntry, ...] = ()
    safety_fails: bool = False
    observations: tuple[HealthObservation, ...] = ()  # observation_health — 증상 기록
    courses: tuple[CourseFactory, ...] = ()  # 진행 중 복약 — 날짜는 오늘에 맞춘다
    nickname: str = "별이"
    # 아직 세계에 못 넣은 데이터. 그 tool 이슈가 채운다 (결과에 note 로 남는다)
    pending: str = ""


WORLDS: dict[str, WorldSpec] = {
    "C48": WorldSpec(observations=_FEVER_THREE_DAYS, courses=(antibiotic,)),
    "C48-2med": WorldSpec(courses=(antibiotic, syrup)),
    "C48-nomed": WorldSpec(),
    # T19 응급 카드 — C48 에 알레르기 · 만성질환을 더했다. 카드는 등록된 값을 옮기는 게 전부다
    "C48-card": WorldSpec(safety=(_PEANUT, _ASTHMA), courses=(antibiotic,)),
    "C48-fever": WorldSpec(courses=(antibiotic,), pending=_FEVER_PENDING),
    "C48-norecord": WorldSpec(pending=_FEVER_PENDING),
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
    **{f"C{months}": WorldSpec(months=months) for months in (2, 20, 23, 24, 35, 36, 71, 72)},
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
            memory=InMemoryHealthMemory(spec.observations),
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
    )
