"""회차 체크 · 해제 — `PUT /children/{cid}/medication-doses/{did}/check` (#577).

게이트가 아니고 키가 없다. 같은 상태를 두 번 보내도 결과가 같고 처음 체크 시각이 남는다.
아직 오지 않은 날의 회차는 422 dose_not_due 다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.v1.routers import medications as medications_router
from app.domains.medication.models import (
    MedicationDose,
)
from app.domains.medication.repository import list_doses, stop_schedule

from ..conftest import issue_bearer, link_child
from .helpers import (
    LATER,
    NOW,
    TODAY,
    check_url,
    dose_of,
    get_courses,
    put_check,
    seed_course,
)


async def test_체크하면_200_과_회차를_돌려준다(db_client, session, bearer, consented):
    _, dose = await dose_of(session, consented)

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"id", "dose_date", "scheduled_time", "checked_at", "checkable"}
    assert data["id"] == str(dose.id)
    assert (data["dose_date"], data["scheduled_time"]) == (TODAY.isoformat(), "08:30")
    assert datetime.fromisoformat(data["checked_at"]) == NOW
    assert data["checkable"] is True


async def test_해제하면_체크_시각이_null(db_client, session, bearer, consented):
    _, dose = await dose_of(session, consented)
    await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": False})

    assert response.status_code == 200
    assert response.json()["checked_at"] is None


async def test_같은_상태를_두_번_보내도_같고_처음_체크_시각이_남는다(
    db_client, session, bearer, consented, monkeypatch
):
    _, dose = await dose_of(session, consented)
    first = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})
    monkeypatch.setattr(medications_router, "utc_now", lambda: LATER)

    again = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert again.status_code == 200
    assert again.json() == first.json()  # LATER 가 아니라 처음 시각이다


async def test_체크는_Idempotency_Key_없이_된다(db_client, session, bearer, consented):
    _, dose = await dose_of(session, consented)

    response = await db_client.put(
        check_url(consented, dose.id), json={"checked": True}, headers=bearer[0]
    )

    assert response.status_code == 200


async def test_체크한_뒤_조회에도_체크_시각이_보인다(db_client, session, bearer, consented):
    course, dose = await dose_of(session, consented)
    await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    (listed,) = (await get_courses(db_client, bearer[0], consented)).json()["courses"]

    checked = {d["id"]: d["checked_at"] for d in listed["doses"]}
    assert datetime.fromisoformat(checked[str(dose.id)]) == NOW
    assert sorted(v is None for v in checked.values()).count(False) == 1  # 나머지는 미체크


async def test_응답을_보낸_뒤에는_체크가_확정돼_롤백해도_남는다(
    db_client, session, bearer, consented
):
    _, dose = await dose_of(session, consented)
    dose_id = dose.id
    await put_check(db_client, bearer[0], consented, dose_id, {"checked": True})

    await session.rollback()

    row = await session.scalar(select(MedicationDose).where(MedicationDose.id == dose_id))
    assert row.checked_at == NOW


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "checked"),
        ({"checked": None}, "checked"),
        ({"checked": "yes"}, "checked"),
        ({"checked": "true"}, "checked"),
        ({"checked": 1}, "checked"),
        ({"checked": True, "note": "x"}, "note"),
    ],
)
async def test_본문이_틀리면_400_이고_칸_이름이_온다(
    db_client, session, bearer, consented, payload, field
):
    _, dose = await dose_of(session, consented)

    response = await put_check(db_client, bearer[0], consented, dose.id, payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert field in response.json()["error"]["detail"]["fields"]


async def test_아직_오지_않은_날의_회차는_422_dose_not_due(db_client, session, bearer, consented):
    _, dose = await dose_of(session, consented, days=1)

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "dose_not_due"
    row = await session.scalar(
        select(MedicationDose.checked_at)
        .where(MedicationDose.id == dose.id)
        .execution_options(populate_existing=True)
    )
    assert row is None


async def test_없는_회차는_404_not_found(db_client, bearer, consented):
    response = await put_check(db_client, bearer[0], consented, uuid.uuid4(), {"checked": True})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_다른_아이의_회차는_404(db_client, session, bearer, consented):
    _, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    _, dose = await dose_of(session, other_child)

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 404  # 내 아이 주소로 남의 회차를 불러도 있는지 알리지 않는다


async def test_멈춘_코스의_회차는_404(db_client, session, bearer, consented):
    course, dose = await dose_of(session, consented)
    await stop_schedule(
        session, child_id=consented, schedule_id=course.id, stopped_at=datetime.now(UTC)
    )

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 404


async def test_끝난_코스의_회차는_조회에_안_보여도_체크된다(db_client, session, bearer, consented):
    course = await seed_course(
        session, consented, starts=-3, ends=-1
    )  # 어제 끝났고 status 는 active
    doses = await list_doses(session, schedule_ids=[course.id])
    last_night = next(d for d in doses if d.dose_date == TODAY - timedelta(days=1))

    listed = (await get_courses(db_client, bearer[0], consented)).json()["courses"]
    response = await put_check(db_client, bearer[0], consented, last_night.id, {"checked": True})

    assert listed == []  # 조회는 끝난 코스를 싣지 않는다
    assert response.status_code == 200  # 그래도 id 를 아는 화면은 체크할 수 있다
    assert datetime.fromisoformat(response.json()["checked_at"]) == NOW


async def test_남의_아이_주소면_403_이고_인증이_없으면_401(db_client, session, bearer, consented):
    _, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    _, dose = await dose_of(session, other_child)

    denied = await put_check(db_client, bearer[0], other_child, dose.id, {"checked": True})
    anonymous = await db_client.put(check_url(consented, dose.id), json={"checked": True})

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "child_access_denied"
    assert anonymous.status_code == 401


@pytest.mark.parametrize("which", ["cid", "did"])
async def test_경로_id_형식이_틀리면_422(db_client, bearer, consented, which):
    url = (
        f"/api/v1/children/not-a-uuid/medication-doses/{uuid.uuid4()}/check"
        if which == "cid"
        else f"/api/v1/children/{consented}/medication-doses/not-a-uuid/check"
    )

    response = await db_client.put(url, json={"checked": True}, headers=bearer[0])

    assert response.status_code == 422


async def test_동의가_없으면_403_consent_required_이고_체크하지_않는다(
    db_client, session, bearer, cid
):
    _, dose = await dose_of(session, cid)

    response = await put_check(db_client, bearer[0], cid, dose.id, {"checked": True})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"
    row = await session.scalar(
        select(MedicationDose.checked_at)
        .where(MedicationDose.id == dose.id)
        .execution_options(populate_existing=True)
    )
    assert row is None


async def test_본문_오류가_동의_없음보다_먼저_걸린다(db_client, session, bearer, cid):
    _, dose = await dose_of(session, cid)

    response = await put_check(db_client, bearer[0], cid, dose.id, {"checked": "yes"})

    assert response.status_code == 400


async def test_동의_없음이_회차_없음보다_먼저_걸린다(db_client, bearer, cid):
    response = await put_check(db_client, bearer[0], cid, uuid.uuid4(), {"checked": True})

    assert response.status_code == 403


async def test_회차_없음이_아직_오지_않음보다_먼저_걸린다(db_client, session, bearer, consented):
    _, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    _, dose = await dose_of(session, other_child, days=2)

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 404
