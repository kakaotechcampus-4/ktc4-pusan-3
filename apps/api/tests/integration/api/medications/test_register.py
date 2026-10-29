"""복약 코스 등록 — `POST /children/{cid}/medications` (#577 · 웹 #563 합의).

화면(`apps/web/src/lib/api/types.ts` 의 SubmitMedicationBody · SubmitMedicationResponse)이 기대하는
모양을 지킨다. 서버가 칸을 더하지 않는다.

여기서 지키는 것 (루트 CLAUDE.md §2).
    ① 승인 게이트 ㉡ — 보호자가 확인한 것만, Idempotency-Key 필수, 같은 키는 처음 응답
    ② 건강정보 동의(child_health)가 없으면 저장하지 않는다
    ③ 틀린 입력은 400 + 틀린 칸 이름이고 저장은 0건이다
    ④ 같은 아이 · 같은 약 · 같은 기간의 진행 코스는 409 already_exists
    ⑤ 검사 순서는 접근 → 본문 형식 → 키 → 재생 → 동의 → 내용 → 중복 (알레르기 등록과 같다)

provisional — #563 에서 정하지 못한 것: 기간 전체가 과거인 코스의 거절, 오늘 지난 시각의 회차,
중복의 기준. 해당 테스트 이름에 표시했다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.symptom_codes import symptom_code_set
from app.domains.medication.models import (
    MedicationDose,
    MedicationSchedule,
)
from app.domains.medication.repository import stop_schedule

from ..conftest import issue_bearer, link_child
from .helpers import (
    ENDS,
    MEALS,
    STARTS,
    TODAY,
    body,
    count,
    grant_child_health,
    medications_url,
    nothing_saved,
    post,
    with_key,
)


async def test_등록하면_201_과_코스를_돌려준다(db_client, bearer, consented):
    headers, _ = bearer

    response = await post(db_client, headers, consented, body())

    assert response.status_code == 201
    course = response.json()["course"]
    assert uuid.UUID(course["id"])
    assert (course["title"], course["dosage"]) == ("아목시실린", "5ml")
    assert (course["starts_on"], course["ends_on"]) == (STARTS.isoformat(), ENDS.isoformat())
    assert course["notice_times"] == ["08:30", "12:30", "19:30"]
    # 내일부터인 코스는 오늘까지의 회차가 아직 없다
    assert course["doses"] == []


async def test_오늘_시작하면_오늘의_회차가_체크_가능으로_온다_provisional(
    db_client, bearer, consented
):
    headers, _ = bearer
    payload = body(schedule={"starts_on": TODAY.isoformat(), "ends_on": ENDS.isoformat()})

    response = await post(db_client, headers, consented, payload)

    doses = response.json()["course"]["doses"]
    assert [(d["dose_date"], d["scheduled_time"]) for d in doses] == [
        (TODAY.isoformat(), "08:30"),
        (TODAY.isoformat(), "12:30"),
        (TODAY.isoformat(), "19:30"),
    ]
    assert all(d["checkable"] is True and d["checked_at"] is None for d in doses)
    assert all(uuid.UUID(d["id"]) for d in doses)


async def test_회차는_기간의_날마다_저장된다(db_client, session, bearer, consented):
    headers, _ = bearer

    await post(db_client, headers, consented, body())

    assert await count(session, MedicationSchedule, consented) == 1
    assert await count(session, MedicationDose) == 9  # 3일 × 하루 3번


async def test_코스는_보호자가_만든_것으로_출처와_함께_저장된다(
    db_client, session, bearer, consented
):
    headers, _ = bearer

    await post(db_client, headers, consented, body(source="prescription_ocr"))

    saved = await session.scalar(select(MedicationSchedule))
    assert saved.created_by == "caregiver"
    assert saved.source == "prescription_ocr"
    assert saved.child_id == consented


async def test_응답은_계약의_칸만_싣는다(db_client, bearer, consented):
    headers, _ = bearer
    payload = body(schedule={"starts_on": TODAY.isoformat()})

    course = (await post(db_client, headers, consented, payload)).json()["course"]

    assert set(course) == {"id", "title", "dosage", "starts_on", "ends_on", "notice_times", "doses"}
    assert set(course["doses"][0]) == {
        "id",
        "dose_date",
        "scheduled_time",
        "checked_at",
        "checkable",
    }


async def test_빈_문자열_선택_칸은_null_로_저장한다(db_client, session, bearer, consented):
    headers, _ = bearer
    payload = body(schedule={"dosage": "", "indication_text": "", "note": ""})

    response = await post(db_client, headers, consented, payload)

    assert response.json()["course"]["dosage"] is None
    saved = await session.scalar(select(MedicationSchedule))
    assert (saved.dosage, saved.indication_text, saved.note) == (None, None, None)


async def test_응답을_보낸_뒤에는_저장이_확정돼_롤백해도_남는다(
    db_client, session, bearer, consented
):
    # 라우터가 commit 을 빠뜨리면 요청이 끝나는 순간 코스가 사라진다.
    # 테스트 세션은 공유라 롤백으로 확인한다
    await post(db_client, bearer[0], consented, body())

    await session.rollback()

    assert await count(session, MedicationSchedule, consented) == 1
    assert await count(session, MedicationDose) == 9


async def test_같은_키로_다시_보내면_처음_응답을_그대로_돌려주고_행이_늘지_않는다(
    db_client, session, bearer, consented
):
    headers, _ = bearer
    first = await post(db_client, headers, consented, body())

    again = await post(db_client, headers, consented, body())

    assert again.status_code == 201
    assert again.json() == first.json()
    assert await count(session, MedicationSchedule, consented) == 1
    assert await count(session, MedicationDose) == 9


async def test_같은_아이를_돌보는_다른_보호자의_같은_키는_재생이_아니다(
    db_client, session, bearer, consented
):
    first = await post(db_client, bearer[0], consented, body(), key="medication-key-1")
    other_headers, other_parent = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other_parent, child_id=consented)

    # 같은 아이 · 같은 주소 · 같은 키다 — 보호자가 다르면 처음 응답을 돌려주지 않고 새 요청으로 본다
    # (그 새 요청은 같은 코스라 중복이다)
    other = await post(db_client, other_headers, consented, body(), key="medication-key-1")

    assert first.status_code == 201
    assert other.status_code == 409


async def test_인증이_없으면_401(db_client, consented):
    response = await db_client.post(
        medications_url(consented), json=body(), headers={"Idempotency-Key": "k"}
    )

    assert response.status_code == 401


async def test_남의_아이면_403_child_access_denied(db_client, session, bearer, consented):
    other_headers, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)

    response = await post(db_client, bearer[0], other_child, body())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"
    assert await nothing_saved(session)


async def test_경로의_아이_id_형식이_틀리면_422(db_client, bearer):
    response = await db_client.post(
        "/api/v1/children/not-a-uuid/medications", json=body(), headers=with_key(bearer[0])
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (lambda: {**body(), "draft_id": "x"}, "draft_id"),
        (lambda: {**body(), "notice_times": ["08:30"]}, "notice_times"),
        (lambda: {**body(), "missing": []}, "missing"),
        (lambda: {**body(), "before": None}, "before"),
        (lambda: body(schedule={"scheduled_time": "08:30"}), "schedule.scheduled_time"),
        (
            lambda: body(doses=[{**MEALS[0], "scheduled_time": "08:30"}]),
            "doses.0.scheduled_time",
        ),
    ],
)
async def test_모르는_칸을_보내면_400_이고_칸_이름이_온다(
    db_client, session, bearer, consented, payload, field
):
    response = await post(db_client, bearer[0], consented, payload())

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert field in response.json()["error"]["detail"]["fields"]
    assert await nothing_saved(session)


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (lambda: {"doses": MEALS, "source": "utterance"}, "schedule"),
        (lambda: {k: v for k, v in body().items() if k != "source"}, "source"),
        (lambda: body(source="manual"), "source"),
        (lambda: body(schedule={"starts_on": "2026-13-01"}), "schedule.starts_on"),
        (lambda: body(schedule={"ends_on": None}), "schedule.ends_on"),
        (lambda: body(schedule={"storage": "freezer"}), "schedule.storage"),
        (lambda: body(schedule={"med_form": "syrup"}), "schedule.med_form"),
        (
            lambda: body(doses=[{**MEALS[0], "timing_anchor": "noon"}]),
            "doses.0.timing_anchor",
        ),
        (
            lambda: body(
                doses=[{"timing_anchor": "fixed", "offset_minutes": 0, "fixed_time": "21:00:30"}]
            ),
            "doses.0.fixed_time",
        ),
        (lambda: body(doses=[{**MEALS[0], "offset_minutes": "thirty"}]), "doses.0.offset_minutes"),
    ],
)
async def test_형식이_틀리면_400_이고_칸_이름이_온다(
    db_client, session, bearer, consented, payload, field
):
    response = await post(db_client, bearer[0], consented, payload())

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert field in response.json()["error"]["detail"]["fields"]
    assert await nothing_saved(session)


async def test_본문_형식_오류가_키_누락보다_먼저_걸린다(db_client, bearer, consented):
    response = await db_client.post(
        medications_url(consented), json={"source": "utterance"}, headers=bearer[0]
    )

    assert response.json()["error"]["code"] == "validation_failed"


async def test_키가_없으면_400_idempotency_key_required(db_client, session, bearer, consented):
    response = await db_client.post(medications_url(consented), json=body(), headers=bearer[0])

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    assert await nothing_saved(session)


async def test_동의가_없으면_403_consent_required_이고_저장하지_않는다(
    db_client, session, bearer, cid
):
    response = await post(db_client, bearer[0], cid, body())

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "consent_required"
    assert error["detail"] == {"scopes": ["child_health"]}
    assert await nothing_saved(session)


async def test_키_누락이_동의_없음보다_먼저_걸린다(db_client, bearer, cid):
    response = await db_client.post(medications_url(cid), json=body(), headers=bearer[0])

    assert response.json()["error"]["code"] == "idempotency_key_required"


async def test_동의_없음이_내용_검증보다_먼저_걸린다(db_client, bearer, cid):
    response = await post(db_client, bearer[0], cid, body(schedule={"title": " "}))

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("payload", "fields"),
    [
        (lambda: body(schedule={"title": "   "}), ["schedule.title"]),
        (lambda: body(schedule={"title": "가" * 201}), ["schedule.title"]),
        (lambda: body(schedule={"dosage": "가" * 201}), ["schedule.dosage"]),
        (lambda: body(schedule={"indication_text": "가" * 501}), ["schedule.indication_text"]),
        (lambda: body(schedule={"note": "가" * 1001}), ["schedule.note"]),
        (
            lambda: body(schedule={"ends_on": (STARTS - timedelta(days=1)).isoformat()}),
            ["schedule.starts_on", "schedule.ends_on"],
        ),
        (
            lambda: body(schedule={"ends_on": (STARTS + timedelta(days=90)).isoformat()}),
            ["schedule.starts_on", "schedule.ends_on"],
        ),
        (lambda: body(doses=[]), ["doses"]),
        (
            lambda: body(
                doses=[
                    {"timing_anchor": "fixed", "offset_minutes": 0, "fixed_time": f"{h:02d}:00"}
                    for h in range(24)
                ]
                + [{"timing_anchor": "fixed", "offset_minutes": 0, "fixed_time": "00:30"}]
            ),
            ["doses"],
        ),
        (
            lambda: body(
                doses=[{"timing_anchor": "fixed", "offset_minutes": 0, "fixed_time": None}]
            ),
            ["doses"],
        ),
        (lambda: body(doses=[{**MEALS[0], "offset_minutes": 181}]), ["doses"]),
        (lambda: body(doses=[MEALS[0], MEALS[0]]), ["doses"]),
        (
            lambda: body(
                doses=[
                    {"timing_anchor": "breakfast", "offset_minutes": 30, "fixed_time": None},
                    {"timing_anchor": "fixed", "offset_minutes": 0, "fixed_time": "08:30"},
                ]
            ),
            ["doses"],
        ),
    ],
)
async def test_내용이_틀리면_400_이고_틀린_칸의_이름이_오고_저장하지_않는다(
    db_client, session, bearer, consented, payload, fields
):
    response = await post(db_client, bearer[0], consented, payload())

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["detail"] == {"fields": fields}
    assert await nothing_saved(session)


async def test_사전에_없는_증상_코드는_400(db_client, session, bearer, consented):
    payload = body(schedule={"symptom_codes": ["not_in_dictionary"]})

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 400
    assert "schedule.symptom_codes" in response.json()["error"]["detail"]["fields"]
    assert await nothing_saved(session)


@pytest.mark.parametrize("codes", [[""], ["  "], ["fever", "fever"]])
async def test_빈_코드나_겹친_코드는_400_이고_칸_이름이_온다(
    db_client, session, bearer, consented, codes
):
    payload = body(schedule={"symptom_codes": codes})

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 400
    assert "schedule.symptom_codes" in response.json()["error"]["detail"]["fields"]
    assert await nothing_saved(session)


async def test_사전_안의_증상_코드는_저장된다(db_client, session, bearer, consented):
    code = sorted(symptom_code_set())[0]

    response = await post(db_client, bearer[0], consented, body(schedule={"symptom_codes": [code]}))

    assert response.status_code == 201
    assert (await session.scalar(select(MedicationSchedule))).symptom_codes == [code]


async def test_기간_전체가_과거면_400_provisional(db_client, session, bearer, consented):
    payload = body(
        schedule={
            "starts_on": (TODAY - timedelta(days=3)).isoformat(),
            "ends_on": (TODAY - timedelta(days=1)).isoformat(),
        }
    )

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": ["schedule.ends_on"]}
    assert await nothing_saved(session)


async def test_같은_코스를_다른_키로_또_보내면_409_already_exists(
    db_client, session, bearer, consented
):
    headers, _ = bearer
    await post(db_client, headers, consented, body(), key="medication-key-1")

    response = await post(db_client, headers, consented, body(), key="medication-key-2")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_exists"
    assert await count(session, MedicationSchedule, consented) == 1
    assert await count(session, MedicationDose) == 9


async def test_멈춘_뒤에는_같은_코스를_다시_등록할_수_있다(db_client, session, bearer, consented):
    headers, _ = bearer
    first = (await post(db_client, headers, consented, body(), key="medication-key-1")).json()
    await stop_schedule(
        session,
        child_id=consented,
        schedule_id=uuid.UUID(first["course"]["id"]),
        stopped_at=datetime.now(UTC),
    )

    again = await post(db_client, headers, consented, body(), key="medication-key-2")

    assert again.status_code == 201


async def test_내용_오류가_중복보다_먼저_걸린다(db_client, bearer, consented):
    headers, _ = bearer
    await post(db_client, headers, consented, body(), key="medication-key-1")

    response = await post(db_client, headers, consented, body(doses=[]), key="medication-key-2")

    assert response.status_code == 400


async def test_다른_아이는_같은_코스를_등록할_수_있다(db_client, session, bearer, consented):
    first = await post(db_client, bearer[0], consented, body(), key="medication-key-1")
    other_headers, other_parent = await issue_bearer(session, token="test-token-other")
    other_child = await link_child(session, parent_id=other_parent)
    await grant_child_health(session, child_id=other_child, parent_id=other_parent)

    # 같은 키여도 보호자가 다르면 재생이 아니라 새 등록이다
    response = await post(db_client, other_headers, other_child, body(), key="medication-key-1")

    assert response.status_code == 201
    assert response.json()["course"]["id"] != first.json()["course"]["id"]
