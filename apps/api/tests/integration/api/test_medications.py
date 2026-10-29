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

등장하는 사람은 전부 테스트용 가짜다. 약 이름은 예시 값이다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.api import idempotency
from app.api.v1.routers import medications as medications_router
from app.core.symptom_codes import symptom_code_set
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import record_consent
from app.domains.medication.models import (
    MedicationCreatedBy,
    MedicationDose,
    MedicationSchedule,
    MedicationSource,
    TimingAnchor,
)
from app.domains.medication.repository import NewDose, add_course, list_doses, stop_schedule
from app.domains.policy.repository import find_active_versions
from app.main import app

from .conftest import issue_bearer, link_child

REAL_KST_TODAY = medications_router.kst_today  # 아래 autouse 픽스처가 바꿔 끼우기 전의 함수

TODAY = date(2026, 10, 27)
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


@pytest.fixture(autouse=True)
def _clean_keys():
    idempotency.clear()
    yield
    idempotency.clear()


NOW = datetime(2026, 10, 27, 0, 40, tzinfo=UTC)  # 한국 시간 09:40 — TODAY 와 같은 날이다
LATER = NOW + timedelta(hours=3)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(medications_router, "kst_today", lambda: TODAY)
    monkeypatch.setattr(medications_router, "utc_now", lambda: NOW)


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


@pytest.fixture
async def consented(session, bearer, cid):
    """보호자 1 이 아이 A 에 건강정보 동의를 해 둔 상태."""
    _, parent_1 = bearer
    await grant_child_health(session, child_id=cid, parent_id=parent_1)
    return cid


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


# ── 1. 정상 ──────────────────────────────────────────────────────────────


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


# ── 2. 같은 키 ───────────────────────────────────────────────────────────


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


def test_kst_오늘은_UTC_날짜가_아니라_한국_날짜다(monkeypatch):
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC 로는 10/26 16:00, 한국 시간으로는 10/27 01:00 — 날짜가 갈리는 시각이다
            return datetime(2026, 10, 26, 16, 0, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(medications_router, "datetime", FakeDateTime)

    assert REAL_KST_TODAY() == date(2026, 10, 27)


# ── 3. 접근 · 인증 ───────────────────────────────────────────────────────


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


# ── 4. 본문 형식 ─────────────────────────────────────────────────────────


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


# ── 5. 키 · 동의 ─────────────────────────────────────────────────────────


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


# ── 6. 내용 검증 ─────────────────────────────────────────────────────────


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


# ── 7. 중복 ──────────────────────────────────────────────────────────────


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


# ── 8. Swagger ───────────────────────────────────────────────────────────

REQUEST_SCHEMAS = ["SubmitMedicationRequest", "MedicationScheduleIn", "MedicationDoseIn"]
RESPONSE_SCHEMAS = ["SubmitMedicationResponse", "MedicationCourseOut", "MedicationDoseOut"]


def _operation() -> dict:
    return app.openapi()["paths"]["/api/v1/children/{cid}/medications"]["post"]


def _has_description(prop: dict) -> bool:
    nodes = [prop, *prop.get("allOf", []), *prop.get("anyOf", [])]
    return any(isinstance(n, dict) and n.get("description", "").strip() for n in nodes)


def test_엔드포인트에_요약_설명_상태_코드가_적혀_있다():
    operation = _operation()

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"201", "400", "403", "409"} <= set(operation["responses"])


def test_Idempotency_Key_헤더가_Swagger_에_보인다():
    names = {p["name"].lower() for p in _operation().get("parameters", [])}

    assert "idempotency-key" in names


@pytest.mark.parametrize("name", REQUEST_SCHEMAS + RESPONSE_SCHEMAS)
def test_요청과_응답의_모든_칸에_설명이_있다(name):
    schemas = app.openapi()["components"]["schemas"]

    assert name in schemas
    assert schemas[name].get("description", "").strip(), f"{name} 에 설명이 없다"
    missing = [
        field for field, prop in schemas[name]["properties"].items() if not _has_description(prop)
    ]
    assert missing == [], f"{name} 설명이 없는 칸: {missing}"


# ── 9. 조회 GET /children/{cid}/medications ──────────────────────────────

LIST_RESPONSE_SCHEMAS = ["MedicationsResponse"]


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


def test_조회_엔드포인트와_응답의_모든_칸에_설명이_있다():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/children/{cid}/medications"]["get"]

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"200", "403"} <= set(operation["responses"])
    for name in LIST_RESPONSE_SCHEMAS:
        schema = spec["components"]["schemas"][name]
        assert schema.get("description", "").strip(), f"{name} 에 설명이 없다"
        missing = [f for f, prop in schema["properties"].items() if not _has_description(prop)]
        assert missing == [], f"{name} 설명이 없는 칸: {missing}"


# ── 10. 회차 체크 PUT /children/{cid}/medication-doses/{did}/check ────────

CHECK_REQUEST_SCHEMAS = ["CheckDoseRequest"]


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


def test_체크_엔드포인트와_요청_응답의_모든_칸에_설명이_있다():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/children/{cid}/medication-doses/{did}/check"]["put"]

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"200", "400", "403", "404", "422"} <= set(operation["responses"])
    for name in CHECK_REQUEST_SCHEMAS:
        schema = spec["components"]["schemas"][name]
        assert schema.get("description", "").strip(), f"{name} 에 설명이 없다"
        missing = [f for f, prop in schema["properties"].items() if not _has_description(prop)]
        assert missing == [], f"{name} 설명이 없는 칸: {missing}"


# ── 11. 수정 PATCH /children/{cid}/medications/{sid} ─────────────────────

REVISE_SCHEMAS = ["ReviseMedicationRequest"]


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


def test_수정_엔드포인트와_요청의_모든_칸에_설명이_있다():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/children/{cid}/medications/{sid}"]["patch"]

    assert operation["summary"].strip()
    assert len(operation["description"].strip()) > 50
    assert {"200", "400", "403", "404", "409"} <= set(operation["responses"])
    assert "idempotency-key" in {p["name"].lower() for p in operation["parameters"]}
    for name in REVISE_SCHEMAS:
        schema = spec["components"]["schemas"][name]
        assert schema.get("description", "").strip(), f"{name} 에 설명이 없다"
        missing = [f for f, prop in schema["properties"].items() if not _has_description(prop)]
        assert missing == [], f"{name} 설명이 없는 칸: {missing}"


# ── 12. 시계 · 끝난 코스 (수정) ──────────────────────────────────────────


async def test_수정은_요청_도중_자정을_넘어도_이미_지난_날의_회차를_지우지_않는다(
    db_client, session, bearer, consented, monkeypatch
):
    course = await seed_course(session, consented, starts=-1, ends=1)  # 오늘 = 10/27
    # 요청이 자정을 넘는 순간 — 두 시계가 다른 날을 가리킨다: kst_today() 는 10/27,
    # utc_now() 는 한국 시간 10/28 00:00:00.5
    monkeypatch.setattr(medications_router, "kst_today", lambda: TODAY)
    monkeypatch.setattr(
        medications_router, "utc_now", lambda: datetime(2026, 10, 27, 15, 0, 0, 500000, tzinfo=UTC)
    )

    response = await patch(db_client, bearer[0], consented, course.id, revise_body())

    assert response.status_code == 200
    kept = [
        d.scheduled_time.strftime("%H:%M")
        for d in await list_doses(session, schedule_ids=[course.id])
        if d.dose_date == TODAY
    ]
    assert kept == ["08:30", "12:30", "19:30"]  # 10/27 은 이미 지났다 — 하나도 지우지 않는다


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


async def test_체크는_요청_도중_자정을_넘으면_새_날의_회차를_오늘로_본다(
    db_client, session, bearer, consented, monkeypatch
):
    # 두 시계가 다른 날을 가리키는 순간 — kst_today() 는 10/27,
    # utc_now() 는 한국 시간 10/28 00:00:00.5. 시계를 한 번만 읽으므로 "오늘" 은 10/28 이고
    # 10/28 의 회차는 체크할 수 있다
    _, dose = await dose_of(session, consented, days=1)  # TODAY(10/27) 의 다음 날 = 10/28
    monkeypatch.setattr(medications_router, "kst_today", lambda: TODAY)
    monkeypatch.setattr(
        medications_router, "utc_now", lambda: datetime(2026, 10, 27, 15, 0, 0, 500000, tzinfo=UTC)
    )

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 200
