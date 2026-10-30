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
from sqlalchemy import func, select

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


# ── 4-2. DB 가 받지 못하는 값 — 요청 모델이 400 으로 막는다 ──────────────

FIXED_AT = {"timing_anchor": "fixed", "offset_minutes": 0}


@pytest.mark.parametrize("value", ["21:00Z", "21:00+09:00", "21:00-05:00"])
async def test_시간대가_붙은_시각은_400(db_client, session, bearer, consented, value):
    # 시간대를 버리고 21:00 으로 저장하면 `21:00Z` 가 한국 06:00 이 아니라 21:00 이 된다
    payload = body(doses=[{**FIXED_AT, "fixed_time": value}])

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": ["doses.0.fixed_time"]}
    assert await nothing_saved(session)


async def test_같은_시각을_시간대만_달리_보내도_500이_아니라_400(
    db_client, session, bearer, consented
):
    # 시간대가 다르면 서로 다른 시각으로 보여 중복 검사를 지나지만
    # DB 에는 둘 다 21:00 이라 UNIQUE 위반이다
    doses = [{**FIXED_AT, "fixed_time": "21:00"}, {**FIXED_AT, "fixed_time": "21:00Z"}]

    response = await post(db_client, bearer[0], consented, body(doses=doses))

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": ["doses.1.fixed_time"]}
    assert await nothing_saved(session)


async def test_시간대_없는_시각은_그대로_저장된다(db_client, bearer, consented):
    payload = body(doses=[{**FIXED_AT, "fixed_time": "21:00"}])

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 201
    assert response.json()["course"]["notice_times"] == ["21:00"]


@pytest.mark.parametrize("field", ["title", "dosage", "indication_text", "note"])
async def test_NUL_이_든_값은_500이_아니라_400(db_client, session, bearer, consented, field):
    # Postgres text 칸이 NUL 을 받지 못해 500 이 나고 ERROR 로그가 남는다 (일기 · 신고와 같은 이유)
    payload = body(schedule={field: "약\x00이름"})

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": [f"schedule.{field}"]}
    assert await nothing_saved(session)


async def test_증상_코드에_NUL_이_들어도_500이_아니라_400(db_client, session, bearer, consented):
    # 증상 코드는 사전 검사에서 걸러져 DB 에 닿지 않는다 — 같은 NUL 문제가 이 칸으로는 새지 않는다
    payload = body(schedule={"symptom_codes": ["fever\x00"]})

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {"fields": ["schedule.symptom_codes"]}
    assert await nothing_saved(session)


async def test_탭과_줄바꿈은_막지_않고_그대로_저장한다(db_client, session, bearer, consented):
    payload = body(schedule={"note": "식후\n30분\t복용"})

    response = await post(db_client, bearer[0], consented, payload)

    assert response.status_code == 201
    assert (await session.scalar(select(MedicationSchedule))).note == "식후\n30분\t복용"


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


async def test_멈춘_뒤에_예정된_회차는_404(db_client, session, bearer, consented):
    course, dose = await dose_of(session, consented)  # 오늘 08:30 (한국)
    await stop_schedule(
        session,
        child_id=consented,
        schedule_id=course.id,
        stopped_at=datetime(
            2026, 10, 26, 20, 0, tzinfo=UTC
        ),  # 한국 05:00 에 멈췄다 — 08:30 은 멈춘 뒤
    )

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 404


async def test_멈춘_코스의_멈추기_전_회차는_체크된다(db_client, session, bearer, consented):
    course, dose = await dose_of(session, consented)  # 오늘 08:30 (한국)
    await stop_schedule(
        session,
        child_id=consented,
        schedule_id=course.id,
        stopped_at=datetime(
            2026, 10, 27, 6, 0, tzinfo=UTC
        ),  # 한국 15:00 에 멈췄다 — 08:30 은 멈추기 전
    )

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 200
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
