"""체크한 보호자 — 체크 · 조회 응답의 `checked_by` (#749).

체크한 보호자는 서버가 로그인 보호자로 남기고, 그 아이의 다른 보호자도 다시 조회하면 같은 값을
본다. 이름을 알 수 없으면(이 칸 전의 체크 · 탈퇴 · 이름 없는 계정) `checked_by` 가 통째로 null 이다.
탈퇴의 SET NULL 자체는 `test_medication_tables.py` 가 DB 로 확인한다.
"""

from datetime import datetime

from sqlalchemy import update

from app.domains.child.parents import unlink_parent
from app.domains.identity.models import Parent
from app.domains.medication import service as medication_service
from app.domains.medication.models import MedicationDose

from ..conftest import issue_bearer, link_child
from .helpers import NOW, dose_of, get_courses, put_check


async def name(session, parent_id, nickname: str) -> None:
    """보호자의 부르는 이름 — 가짜 값이다 (루트 CLAUDE.md §9)."""
    await session.execute(update(Parent).where(Parent.id == parent_id).values(nickname=nickname))


async def helper_of(session, child_id, nickname="보호자둘"):
    """같은 아이를 함께 보는 보호자 — (헤더, 보호자 id)."""
    headers, parent_id = await issue_bearer(session, token="test-token-helper")
    await link_child(session, parent_id=parent_id, child_id=child_id)
    await name(session, parent_id, nickname)
    return headers, parent_id


def listed_dose(response, dose_id) -> dict:
    (course,) = response.json()["courses"]
    return next(d for d in course["doses"] if d["id"] == str(dose_id))


async def test_체크하면_응답과_다른_보호자의_조회에_체크한_보호자가_보인다(
    db_client, session, bearer, consented
):
    headers, owner = bearer
    await name(session, owner, "보호자하나")
    helper_headers, _ = await helper_of(session, consented)
    _, dose = await dose_of(session, consented)

    checked = await put_check(db_client, headers, consented, dose.id, {"checked": True})
    seen = listed_dose(await get_courses(db_client, helper_headers, consented), dose.id)

    expected = {"parent_id": str(owner), "nickname": "보호자하나"}
    assert checked.json()["checked_by"] == expected
    assert seen["checked_by"] == expected
    assert datetime.fromisoformat(seen["checked_at"]) == NOW


async def test_이미_체크된_회차에_다른_보호자가_체크해도_처음_보호자가_남는다(
    db_client, session, bearer, consented
):
    headers, owner = bearer
    await name(session, owner, "보호자하나")
    helper_headers, _ = await helper_of(session, consented)
    _, dose = await dose_of(session, consented)
    await put_check(db_client, headers, consented, dose.id, {"checked": True})

    again = await put_check(db_client, helper_headers, consented, dose.id, {"checked": True})

    assert again.json()["checked_by"] == {"parent_id": str(owner), "nickname": "보호자하나"}


async def test_해제하면_체크한_보호자도_비고_다른_보호자가_다시_체크하면_그_보호자다(
    db_client, session, bearer, consented
):
    headers, _ = bearer
    helper_headers, helper = await helper_of(session, consented)
    _, dose = await dose_of(session, consented)
    await put_check(db_client, headers, consented, dose.id, {"checked": True})

    unchecked = await put_check(db_client, headers, consented, dose.id, {"checked": False})
    rechecked = await put_check(db_client, helper_headers, consented, dose.id, {"checked": True})

    assert unchecked.json()["checked_by"] is None
    assert rechecked.json()["checked_by"] == {"parent_id": str(helper), "nickname": "보호자둘"}


async def test_체크한_보호자를_알_수_없으면_시각만_있고_체크한_보호자는_null(
    db_client, session, bearer, consented
):
    """이 칸이 생기기 전의 체크와 탈퇴(SET NULL)는 같은 모양이다. 이름 없는 계정도 null 이다."""
    headers, _ = bearer  # 이름을 정하지 않은 보호자
    _, legacy = await dose_of(session, consented)
    await session.execute(
        update(MedicationDose).where(MedicationDose.id == legacy.id).values(checked_at=NOW)
    )
    nameless = next(
        d
        for d in (await get_courses(db_client, headers, consented)).json()["courses"][0]["doses"]
        if d["id"] != str(legacy.id)
    )

    checked = await put_check(db_client, headers, consented, nameless["id"], {"checked": True})
    seen = listed_dose(await get_courses(db_client, headers, consented), legacy.id)

    assert checked.json()["checked_by"] is None
    assert seen["checked_by"] is None
    assert datetime.fromisoformat(seen["checked_at"]) == NOW


async def test_연결을_끊은_보호자의_체크는_이름이_그대로_보인다(
    db_client, session, bearer, consented
):
    headers, _ = bearer
    helper_headers, helper = await helper_of(session, consented)
    _, dose = await dose_of(session, consented)
    await put_check(db_client, helper_headers, consented, dose.id, {"checked": True})

    assert await unlink_parent(session, child_id=consented, parent_id=helper)
    seen = listed_dose(await get_courses(db_client, headers, consented), dose.id)

    assert seen["checked_by"] == {"parent_id": str(helper), "nickname": "보호자둘"}


async def test_인증_확인_뒤_그사이_탈퇴했으면_401_이고_체크하지_않는다(
    db_client, session, bearer, consented, monkeypatch
):
    headers, _ = bearer
    _, dose = await dose_of(session, consented)

    async def gone(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(medication_service, "lock_checker", gone)
    response = await put_check(db_client, headers, consented, dose.id, {"checked": True})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    await session.refresh(dose)
    assert (dose.checked_at, dose.checked_by) == (None, None)
