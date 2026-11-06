"""내 복약 알림 켜기 · 끄기와 응답의 `notice_enabled` (#749).

알림은 보호자 개인 것이다(일정 알림과 같다). 켜기 `PUT …/notice` · 끄기 `DELETE …/notice`,
둘 다 본문 없이 204 다. 켜기만 건강정보 동의를 확인한다.
"""

from sqlalchemy import update

from app.api.v1.routers import medications as medications_router
from app.domains.medication.models import MedicationSchedule, MedicationStatus
from app.domains.medication.service import NotLinked, subscribe
from tests.integration.api.conftest import issue_bearer, link_child

from .helpers import (
    NOW,
    TODAY,
    body,
    course_url,
    get_courses,
    patch,
    post,
    revise_body,
    seed_course,
)


def notice_url(child_id, course_id) -> str:
    return f"{course_url(child_id, course_id)}/notice"


async def member_of(session, cid):
    """같은 아이를 함께 보는 두 번째 보호자."""
    member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member[1], child_id=cid)
    return member


async def enabled(db_client, headers, cid) -> dict[str, bool]:
    """{코스 id: 그 보호자의 notice_enabled}."""
    data = (await get_courses(db_client, headers, cid)).json()
    return {c["id"]: c["notice_enabled"] for c in data["courses"]}


async def test_등록한_보호자만_켜진_채로_시작한다(db_client, session, bearer, consented):
    member = await member_of(session, consented)

    response = await post(db_client, bearer[0], consented, body(schedule={"starts_on": str(TODAY)}))

    course_id = response.json()["course"]["id"]
    assert response.json()["course"]["notice_enabled"] is True
    assert await enabled(db_client, bearer[0], consented) == {course_id: True}
    assert await enabled(db_client, member[0], consented) == {course_id: False}


async def test_켜고_끄는_것은_내_알림만_바꾸고_두_번_보내도_같다(
    db_client, session, bearer, consented
):
    member = await member_of(session, consented)
    course = await seed_course(session, consented)
    url = notice_url(consented, course.id)

    for _ in range(2):
        assert (await db_client.put(url, headers=member[0])).status_code == 204
    assert await enabled(db_client, member[0], consented) == {str(course.id): True}
    assert await enabled(db_client, bearer[0], consented) == {str(course.id): False}

    for _ in range(2):
        assert (await db_client.delete(url, headers=member[0])).status_code == 204
    assert await enabled(db_client, member[0], consented) == {str(course.id): False}


async def test_수정_응답에도_내_알림이_실린다(db_client, session, bearer, consented):
    course = await seed_course(session, consented)
    await db_client.put(notice_url(consented, course.id), headers=bearer[0])

    response = await patch(db_client, bearer[0], consented, course.id, revise_body())

    assert response.json()["course"]["notice_enabled"] is True


async def test_동의가_없으면_켤_수_없지만_끌_수는_있다(db_client, session, bearer, cid):
    course = await seed_course(session, cid)
    # 동의하고 켠 뒤 철회한 상태 — 철회 API 를 거치지 않고 구독 행만 미리 둔다
    await subscribe(
        session, child_id=cid, course_id=course.id, parent_id=bearer[1], today=TODAY, now=NOW
    )
    url = notice_url(cid, course.id)

    refused = await db_client.put(url, headers=bearer[0])
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "consent_required"

    assert (await db_client.delete(url, headers=bearer[0])).status_code == 204
    assert await enabled(db_client, bearer[0], cid) == {str(course.id): False}


async def test_멈추거나_끝났거나_없는_코스는_켤_수_없다(db_client, session, bearer, consented):
    stopped = await seed_course(session, consented, title="멈춘 약")
    await session.execute(
        update(MedicationSchedule)
        .where(MedicationSchedule.id == stopped.id)
        .values(status=MedicationStatus.STOPPED)
    )
    ended = await seed_course(session, consented, title="끝난 약", starts=-3, ends=-1)
    _, stranger = await issue_bearer(session, token="test-token-stranger")
    others = await seed_course(session, await link_child(session, parent_id=stranger))

    for course_id in (stopped.id, ended.id, others.id):
        response = await db_client.put(notice_url(consented, course_id), headers=bearer[0])
        assert response.status_code == 404, course_id
        assert response.json()["error"]["code"] == "not_found"


async def test_시작_전_코스는_켤_수_있다(db_client, session, bearer, consented):
    course = await seed_course(session, consented, starts=1, ends=2)

    response = await db_client.put(notice_url(consented, course.id), headers=bearer[0])

    assert response.status_code == 204


async def test_내_아이가_아니면_켜지도_끄지도_못한다(db_client, session, bearer, consented):
    _, stranger = await issue_bearer(session, token="test-token-stranger")
    other_child = await link_child(session, parent_id=stranger)
    course = await seed_course(session, other_child)
    url = notice_url(other_child, course.id)

    assert (await db_client.put(url, headers=bearer[0])).status_code == 403
    assert (await db_client.delete(url, headers=bearer[0])).status_code == 403


async def test_확인_뒤_연결이_끊기면_켜기와_등록은_403(
    db_client, session, bearer, consented, monkeypatch
):
    """접근 확인과 저장 사이에 연결이 끊긴 경우 — 서비스의 `NotLinked` 를 403 으로 바꾼다."""

    async def unlinked(*_, **__):
        raise NotLinked

    monkeypatch.setattr(medications_router, "subscribe", unlinked)
    monkeypatch.setattr(medications_router, "create_course", unlinked)
    course = await seed_course(session, consented)

    turned_on = await db_client.put(notice_url(consented, course.id), headers=bearer[0])
    registered = await post(db_client, bearer[0], consented, body())

    for response in (turned_on, registered):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "child_access_denied"
