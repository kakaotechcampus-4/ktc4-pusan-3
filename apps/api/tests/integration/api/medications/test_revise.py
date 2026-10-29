"""복약 코스 수정 — `PATCH /children/{cid}/medications/{sid}` (#577).

최종 상태 전체를 받고 복용 기록(체크)은 지우지 않는다. 키 필수 · 같은 키는 처음 응답(200).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.symptom_codes import symptom_code_set
from app.domains.medication.models import (
    MedicationSchedule,
)
from app.domains.medication.repository import list_doses, stop_schedule

from ..conftest import issue_bearer, link_child
from .helpers import (
    MEALS,
    TODAY,
    course_url,
    get_courses,
    patch,
    put_check,
    revise_body,
    seed_course,
    with_key,
)


async def test_수정하면_200_과_고친_코스를_돌려준다(db_client, session, bearer, consented):
    course = await seed_course(session, consented, starts=-1, ends=1)

    response = await patch(
        db_client,
        bearer[0],
        consented,
        course.id,
        revise_body(schedule={"title": "타이레놀", "dosage": "3ml"}),
    )

    assert response.status_code == 200
    data = response.json()["course"]
    assert data["id"] == str(course.id)
    assert (data["title"], data["dosage"]) == ("타이레놀", "3ml")
    assert set(data) == {"id", "title", "dosage", "starts_on", "ends_on", "notice_times", "doses"}


async def test_수정한_값이_모두_저장된다(db_client, session, bearer, consented):
    course = await seed_course(session, consented, starts=-1, ends=1)
    code = sorted(symptom_code_set())[0]
    schedule = {
        "title": "  타이레놀 ",
        "dosage": "3ml",
        "indication_text": "열",
        "note": "식후",
        "storage": "fridge",
        "med_form": "drops",
        "symptom_codes": [code],
        "ends_on": (TODAY + timedelta(days=2)).isoformat(),
    }

    response = await patch(
        db_client, bearer[0], consented, course.id, revise_body(schedule=schedule)
    )

    assert response.status_code == 200
    await session.refresh(course)
    assert course.title == "  타이레놀 "  # 받은 그대로
    assert (course.dosage, course.indication_text, course.note) == ("3ml", "열", "식후")
    assert (course.storage, course.med_form) == ("fridge", "drops")
    assert course.symptom_codes == [code]
    assert course.ends_on == TODAY + timedelta(days=2)
    assert (course.source, course.created_by) == ("utterance", "caregiver")  # 출처는 그대로다


async def test_수정_뒤_알림_시각은_현재_패턴이고_회차_기록은_옛_것도_남는다(
    db_client, session, bearer, consented
):
    course = await seed_course(session, consented, starts=-1, ends=1)  # 아침 · 점심 · 저녁

    data = (await patch(db_client, bearer[0], consented, course.id, revise_body())).json()["course"]

    assert data["notice_times"] == ["12:30"]  # 점심만 먹는 패턴
    shown = [(d["dose_date"], d["scheduled_time"]) for d in data["doses"]]
    assert shown == [
        ((TODAY - timedelta(days=1)).isoformat(), "08:30"),  # 어제는 옛 패턴 그대로
        ((TODAY - timedelta(days=1)).isoformat(), "12:30"),
        ((TODAY - timedelta(days=1)).isoformat(), "19:30"),
        (TODAY.isoformat(), "08:30"),  # 오늘 지난 시각은 남고
        (TODAY.isoformat(), "12:30"),  # 지금 이후인 시각은 새 패턴으로 다시 만들어졌다
    ]


async def test_수정은_서버의_지금_이전_시각을_다시_만들지_않는다(
    db_client, session, bearer, consented
):
    course = await seed_course(session, consented, starts=-1, ends=1)
    past_today = {
        "timing_anchor": "fixed",
        "offset_minutes": 0,
        "fixed_time": "09:20",
    }  # 지금은 09:40
    future_today = {"timing_anchor": "fixed", "offset_minutes": 0, "fixed_time": "09:41"}

    response = await patch(
        db_client,
        bearer[0],
        consented,
        course.id,
        revise_body(doses=[past_today, future_today]),
    )

    today_times = [
        d["scheduled_time"]
        for d in response.json()["course"]["doses"]
        if d["dose_date"] == TODAY.isoformat()
    ]
    assert "09:20" not in today_times  # 이미 지난 시각은 만들지 않는다
    assert "09:41" in today_times  # 곧 올 시각은 만든다


async def test_체크한_회차는_수정_뒤에도_응답과_조회에_남는다(
    db_client, session, bearer, consented
):
    course = await seed_course(session, consented, starts=-1, ends=1)
    doses = await list_doses(session, schedule_ids=[course.id])
    dinner_today = next(d for d in doses if d.dose_date == TODAY and d.scheduled_time.hour == 19)
    await put_check(db_client, bearer[0], consented, dinner_today.id, {"checked": True})

    response = await patch(db_client, bearer[0], consented, course.id, revise_body())

    ids = {d["id"] for d in response.json()["course"]["doses"]}
    assert str(dinner_today.id) in ids  # 새 패턴에 저녁이 없어도 기록은 남는다
    listed = (await get_courses(db_client, bearer[0], consented)).json()["courses"][0]
    assert str(dinner_today.id) in {d["id"] for d in listed["doses"]}


async def test_수정은_키가_없으면_400(db_client, session, bearer, consented):
    course = await seed_course(session, consented)

    response = await db_client.patch(
        course_url(consented, course.id), json=revise_body(), headers=bearer[0]
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


async def test_수정을_같은_키로_다시_보내면_처음_응답을_재생한다(
    db_client, session, bearer, consented
):
    course = await seed_course(session, consented, starts=-1, ends=1)
    first = await patch(db_client, bearer[0], consented, course.id, revise_body())
    # 그 사이 다른 방법으로 코스가 바뀌어도 같은 키는 처음 응답을 돌려준다
    await session.execute(
        update(MedicationSchedule).where(MedicationSchedule.id == course.id).values(title="바뀜")
    )

    again = await patch(db_client, bearer[0], consented, course.id, revise_body())

    assert again.status_code == 200
    assert again.json() == first.json()


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (lambda: {**revise_body(), "source": "utterance"}, "source"),
        (lambda: {**revise_body(), "draft_id": "x"}, "draft_id"),
        (lambda: {**revise_body(), "before": None}, "before"),
        (lambda: {"doses": [MEALS[0]]}, "schedule"),
        (lambda: {k: v for k, v in revise_body().items() if k != "doses"}, "doses"),
        (lambda: revise_body(schedule={"scheduled_time": "08:30"}), "schedule.scheduled_time"),
        (
            lambda: revise_body(doses=[{**MEALS[0], "scheduled_time": "08:30"}]),
            "doses.0.scheduled_time",
        ),
        (lambda: revise_body(schedule={"ends_on": "20261301"}), "schedule.ends_on"),
        (
            lambda: revise_body(schedule={"symptom_codes": ["not_in_dictionary"]}),
            "schedule.symptom_codes",
        ),
    ],
)
async def test_수정_본문이_틀리면_400_이고_칸_이름이_온다(
    db_client, session, bearer, consented, payload, field
):
    course = await seed_course(session, consented)

    response = await patch(db_client, bearer[0], consented, course.id, payload())

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert field in response.json()["error"]["detail"]["fields"]


async def test_수정_본문_오류가_키_누락보다_먼저_걸린다(db_client, session, bearer, consented):
    course = await seed_course(session, consented)

    response = await db_client.patch(
        course_url(consented, course.id), json={"doses": []}, headers=bearer[0]
    )

    assert response.json()["error"]["code"] == "validation_failed"


async def test_수정은_남의_아이_주소면_403_이고_인증이_없으면_401(
    db_client, session, bearer, consented
):
    _, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    course = await seed_course(session, other_child)

    denied = await patch(db_client, bearer[0], other_child, course.id, revise_body())
    anonymous = await db_client.patch(
        course_url(consented, course.id), json=revise_body(), headers={"Idempotency-Key": "k"}
    )

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "child_access_denied"
    assert anonymous.status_code == 401


@pytest.mark.parametrize("which", ["cid", "sid"])
async def test_수정의_경로_id_형식이_틀리면_422(db_client, bearer, consented, which):
    url = (
        f"/api/v1/children/not-a-uuid/medications/{uuid.uuid4()}"
        if which == "cid"
        else f"/api/v1/children/{consented}/medications/not-a-uuid"
    )

    response = await db_client.patch(url, json=revise_body(), headers=with_key(bearer[0]))

    assert response.status_code == 422


async def test_수정은_동의가_없으면_403_이고_바꾸지_않는다(db_client, session, bearer, cid):
    course = await seed_course(session, cid)

    response = await patch(db_client, bearer[0], cid, course.id, revise_body())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"
    await session.refresh(course)
    assert course.title == "아목시실린"


async def test_없는_코스는_404_not_found(db_client, bearer, consented):
    response = await patch(db_client, bearer[0], consented, uuid.uuid4(), revise_body())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_남의_아이_코스는_404(db_client, session, bearer, consented):
    _, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    course = await seed_course(session, other_child)

    response = await patch(db_client, bearer[0], consented, course.id, revise_body())

    assert response.status_code == 404  # 내 아이 주소로 남의 코스를 불러도 있는지 알리지 않는다


async def test_멈춘_코스는_404(db_client, session, bearer, consented):
    course = await seed_course(session, consented)
    await stop_schedule(
        session, child_id=consented, schedule_id=course.id, stopped_at=datetime.now(UTC)
    )

    response = await patch(db_client, bearer[0], consented, course.id, revise_body())

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("over", "fields"),
    [
        ({"schedule": {"title": " "}}, ["schedule.title"]),
        ({"doses": []}, ["doses"]),
        ({"doses": [MEALS[0], MEALS[0]]}, ["doses"]),
        (
            {"schedule": {"ends_on": (TODAY + timedelta(days=200)).isoformat()}},
            ["schedule.starts_on", "schedule.ends_on"],
        ),
    ],
)
async def test_수정_내용이_틀리면_400_이고_바꾸지_않는다(
    db_client, session, bearer, consented, over, fields
):
    course = await seed_course(session, consented, starts=-1, ends=1)
    before = len(await list_doses(session, schedule_ids=[course.id]))

    response = await patch(db_client, bearer[0], consented, course.id, revise_body(**over))

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": fields}
    assert len(await list_doses(session, schedule_ids=[course.id])) == before


async def test_체크한_회차가_새_기간_밖이_되면_400(db_client, session, bearer, consented):
    course = await seed_course(session, consented, starts=-1, ends=1)
    doses = await list_doses(session, schedule_ids=[course.id])
    yesterday = next(d for d in doses if d.dose_date == TODAY - timedelta(days=1))
    await put_check(db_client, bearer[0], consented, yesterday.id, {"checked": True})

    response = await patch(
        db_client,
        bearer[0],
        consented,
        course.id,
        revise_body(schedule={"starts_on": TODAY.isoformat()}),  # 체크한 어제가 밖이 된다
    )

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {
        "fields": ["schedule.starts_on", "schedule.ends_on"]
    }


async def test_다른_진행_코스와_같아지는_수정은_409_provisional(
    db_client, session, bearer, consented
):
    await seed_course(session, consented, title="아목시실린", starts=-1, ends=1)
    other = await seed_course(session, consented, title="타이레놀", starts=-1, ends=1)

    response = await patch(
        db_client, bearer[0], consented, other.id, revise_body()
    )  # 이름이 같아진다

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_exists"


async def test_수정은_동의_없음이_코스_없음보다_먼저_걸린다(db_client, bearer, cid):
    response = await patch(db_client, bearer[0], cid, uuid.uuid4(), revise_body())

    assert response.status_code == 403  # 동의 없는 아이: 없는 코스여도 403 이다


async def test_수정은_코스_없음이_내용_오류보다_먼저_걸린다(db_client, bearer, consented):
    response = await patch(db_client, bearer[0], consented, uuid.uuid4(), revise_body(doses=[]))

    assert response.status_code == 404


async def test_수정은_내용_오류가_중복보다_먼저_걸린다(db_client, session, bearer, consented):
    await seed_course(session, consented, title="아목시실린", starts=-1, ends=1)
    other = await seed_course(session, consented, title="타이레놀", starts=-1, ends=1)

    response = await patch(db_client, bearer[0], consented, other.id, revise_body(doses=[]))

    assert response.status_code == 400


async def test_수정을_보낸_뒤에는_저장이_확정돼_롤백해도_남는다(
    db_client, session, bearer, consented
):
    course = await seed_course(session, consented, starts=-1, ends=1)
    course_id = course.id
    await patch(
        db_client, bearer[0], consented, course_id, revise_body(schedule={"title": "타이레놀"})
    )

    await session.rollback()

    row = await session.scalar(select(MedicationSchedule).where(MedicationSchedule.id == course_id))
    assert row.title == "타이레놀"


async def test_끝난_코스를_수정하면_404(db_client, session, bearer, consented):
    course = await seed_course(
        session, consented, starts=-3, ends=-1
    )  # 어제 끝났고 status 는 active
    payload = revise_body(
        schedule={
            "starts_on": (TODAY - timedelta(days=3)).isoformat(),
            "ends_on": (TODAY - timedelta(days=1)).isoformat(),
            "title": "아목시실린 수정",
        }
    )

    response = await patch(db_client, bearer[0], consented, course.id, payload)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    await session.refresh(course)
    assert course.title == "아목시실린"
