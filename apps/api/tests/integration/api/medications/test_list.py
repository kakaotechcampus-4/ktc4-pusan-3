"""진행 중인 복약 코스 조회 — `GET /children/{cid}/medications` (#577).

서버의 한국 오늘과 진행 중인 코스, 코스마다 알림 시각과 오늘까지의 회차를 준다. 읽기라 키가 없고
동의도 보지 않는다.
"""

from datetime import timedelta

from ..conftest import issue_bearer, link_child
from .helpers import (
    TODAY,
    get_courses,
    medications_url,
    seed_course,
)


async def test_조회는_서버의_오늘과_진행_중인_코스를_돌려준다(db_client, session, bearer, cid):
    await seed_course(session, cid, title="진행", starts=-1, ends=1)
    await seed_course(session, cid, title="어제 끝남", starts=-4, ends=-1)

    response = await get_courses(db_client, bearer[0], cid)

    assert response.status_code == 200
    data = response.json()
    assert data["today"] == TODAY.isoformat()
    assert [c["title"] for c in data["courses"]] == ["진행"]


async def test_조회의_코스는_알림_시각과_오늘까지의_회차를_싣는다(db_client, session, bearer, cid):
    await seed_course(session, cid, starts=-1, ends=1)

    (course,) = (await get_courses(db_client, bearer[0], cid)).json()["courses"]

    assert course["notice_times"] == ["08:30", "12:30", "19:30"]
    assert [(d["dose_date"], d["scheduled_time"]) for d in course["doses"]] == [
        (day.isoformat(), time)
        for day in (TODAY - timedelta(days=1), TODAY)
        for time in ("08:30", "12:30", "19:30")
    ]
    assert all(d["checkable"] is True for d in course["doses"])


async def test_조회는_코스를_시작일_순으로_돌려주고_id_가_저장된_코스와_같다(
    db_client, session, bearer, cid
):
    late = await seed_course(session, cid, title="늦게 시작", starts=0, ends=2)
    early = await seed_course(session, cid, title="먼저 시작", starts=-3, ends=2)

    courses = (await get_courses(db_client, bearer[0], cid)).json()["courses"]

    assert [(c["id"], c["title"]) for c in courses] == [
        (str(early.id), "먼저 시작"),
        (str(late.id), "늦게 시작"),
    ]


async def test_조회는_코스마다_자기_회차만_싣는다(db_client, session, bearer, cid):
    first = await seed_course(session, cid, title="첫째", starts=-1, ends=0)
    second = await seed_course(session, cid, title="둘째", starts=0, ends=0)

    courses = {c["id"]: c for c in (await get_courses(db_client, bearer[0], cid)).json()["courses"]}

    assert len(courses[str(first.id)]["doses"]) == 6  # 어제 · 오늘 × 3
    assert len(courses[str(second.id)]["doses"]) == 3  # 오늘 × 3


async def test_조회는_코스가_없으면_빈_목록(db_client, bearer, cid):
    response = await get_courses(db_client, bearer[0], cid)

    assert response.status_code == 200
    assert response.json() == {"today": TODAY.isoformat(), "courses": []}


async def test_조회는_동의가_없어도_200(db_client, session, bearer, cid):
    await seed_course(session, cid)

    response = await get_courses(db_client, bearer[0], cid)  # `cid` 는 동의 없는 아이

    assert response.status_code == 200
    assert len(response.json()["courses"]) == 1


async def test_조회는_키를_받지_않는다(db_client, bearer, cid):
    # 읽기라 Idempotency-Key 가 없어도 400 이 아니다
    assert (await get_courses(db_client, bearer[0], cid)).status_code == 200


async def test_조회는_다른_아이의_코스를_싣지_않는다(db_client, session, bearer, cid):
    other_headers, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    await seed_course(session, other_child, title="남의 약")

    mine = await get_courses(db_client, bearer[0], cid)

    assert mine.json()["courses"] == []


async def test_조회는_남의_아이면_403_이고_인증이_없으면_401(db_client, session, bearer, cid):
    _, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)

    assert (await get_courses(db_client, bearer[0], other_child)).status_code == 403
    assert (await db_client.get(medications_url(cid))).status_code == 401


async def test_조회의_경로_아이_id_형식이_틀리면_422(db_client, bearer):
    response = await db_client.get("/api/v1/children/not-a-uuid/medications", headers=bearer[0])

    assert response.status_code == 422


async def test_조회_응답은_계약의_칸만_싣는다(db_client, session, bearer, cid):
    await seed_course(session, cid)

    data = (await get_courses(db_client, bearer[0], cid)).json()

    assert set(data) == {"today", "courses"}
    assert set(data["courses"][0]) == {
        "id",
        "title",
        "dosage",
        "starts_on",
        "ends_on",
        "notice_times",
        "doses",
    }
