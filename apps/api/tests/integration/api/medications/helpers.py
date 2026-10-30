"""복약 API 테스트가 같이 쓰는 상수 · 헬퍼 (#577).

TODAY 는 서버 시계를 고정한 오늘이고 NOW 는 그날 한국 시간 09:40 이다 (conftest 가 바꿔 끼운다).
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import record_consent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_doses
from app.domains.policy.repository import find_active_versions

TODAY = date(2026, 10, 27)
FIXED_AT = {"timing_anchor": "fixed", "offset_minutes": 0}  # 시각만 바꿔 쓰는 dose 의 뼈대


STARTS = TODAY + timedelta(days=1)  # 내일부터


ENDS = TODAY + timedelta(days=3)


MEALS = [
    {"timing_anchor": "breakfast", "offset_minutes": 30, "fixed_time": None},
    {"timing_anchor": "lunch", "offset_minutes": 30, "fixed_time": None},
    {"timing_anchor": "dinner", "offset_minutes": 30, "fixed_time": None},
]


def medications_url(child_id) -> str:
    return f"/api/v1/children/{child_id}/medications"


def with_key(headers: dict, key: str = "medication-key-1") -> dict:
    return {**headers, "Idempotency-Key": key}


def body(*, schedule: dict | None = None, **over) -> dict:
    values = {
        "schedule": {
            "title": "아목시실린",
            "dosage": "5ml",
            "indication_text": "중이염",
            "note": None,
            "storage": None,
            "med_form": "liquid",
            "symptom_codes": [],
            "starts_on": STARTS.isoformat(),
            "ends_on": ENDS.isoformat(),
        }
        | (schedule or {}),
        "doses": MEALS,
        "source": "utterance",
    }
    return values | over


NOW = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)  # 한국 시간 09:40 — TODAY 와 같은 날이다


LATER = NOW + timedelta(hours=3)


async def grant_child_health(session, *, child_id, parent_id) -> None:
    versions = {
        row.scope: row for row in await find_active_versions(session, now=datetime.now(UTC))
    }
    await record_consent(
        session,
        actor_parent_id=parent_id,
        child_id=child_id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=versions[ConsentScope.CHILD_HEALTH].id,
        guardian_attested=True,
    )


async def count(session, model, child_id=None) -> int:
    stmt = select(func.count()).select_from(model)
    if child_id is not None and model is MedicationSchedule:
        stmt = stmt.where(MedicationSchedule.child_id == child_id)
    return await session.scalar(stmt)


async def nothing_saved(session) -> bool:
    return (
        await count(session, MedicationSchedule) == 0 and await count(session, MedicationDose) == 0
    )


async def post(db_client, headers, child_id, payload, key: str = "medication-key-1"):
    return await db_client.post(
        medications_url(child_id), json=payload, headers=with_key(headers, key)
    )


async def seed_course(session, child_id, *, title="아목시실린", starts=-1, ends=1):
    """repository 로 직접 넣는다 — 서비스는 지난 날짜의 회차를 만들지 않는다."""
    days = (ends - starts) + 1
    return await add_course(
        session,
        child_id=child_id,
        title=title,
        starts_on=TODAY + timedelta(days=starts),
        ends_on=TODAY + timedelta(days=ends),
        source=MedicationSource.UTTERANCE,
        created_by=MedicationCreatedBy.CAREGIVER,
        doses=[
            NewDose(TODAY + timedelta(days=starts + n), anchor, 30, None)
            for n in range(days)
            for anchor in (TimingAnchor.BREAKFAST, TimingAnchor.LUNCH, TimingAnchor.DINNER)
        ],
    )


async def get_courses(db_client, headers, child_id, **kwargs):
    return await db_client.get(medications_url(child_id), headers=headers, **kwargs)


def check_url(child_id, dose_id) -> str:
    return f"/api/v1/children/{child_id}/medication-doses/{dose_id}/check"


async def put_check(db_client, headers, child_id, dose_id, payload):
    return await db_client.put(check_url(child_id, dose_id), json=payload, headers=headers)


async def dose_of(session, child_id, *, days=0, **kwargs):
    """오늘(days=0) 또는 그 날짜의 아침 회차 하나."""
    course = await seed_course(session, child_id, starts=min(days, 0), ends=max(days, 0), **kwargs)
    doses = await list_doses(session, schedule_ids=[course.id])
    target = TODAY + timedelta(days=days)
    return course, next(d for d in doses if d.dose_date == target and d.scheduled_time.hour == 8)


def course_url(child_id, course_id) -> str:
    return f"/api/v1/children/{child_id}/medications/{course_id}"


def revise_body(*, schedule: dict | None = None, **over) -> dict:
    """수정 본문 — 등록 본문에서 `source` 만 뺀 것. 기본은 점심 식후만 먹는 패턴으로 바꾼다."""
    base = body(
        schedule={
            "starts_on": (TODAY - timedelta(days=1)).isoformat(),
            "ends_on": (TODAY + timedelta(days=1)).isoformat(),
        }
        | (schedule or {}),
        doses=[MEALS[1]],
    )
    del base["source"]
    return base | over


async def patch(db_client, headers, child_id, course_id, payload, key: str = "revise-key-1"):
    return await db_client.patch(
        course_url(child_id, course_id), json=payload, headers=with_key(headers, key)
    )
