"""Agent 초안 → 프론트 변환 → 서버 — 계약이 실제로 이어지는가 (#577).

다른 테스트는 서버가 정한 모양으로 요청을 만든다. 여기서는 **Agent 가 실제로 만드는 초안**
(`app/core/medication_draft.py` 의 `MedicationDraft`)을 **프론트가 제출 본문으로 바꾸는 규칙**
(`apps/web/src/lib/medication.ts` 의 `submitBodyOf`, #563)대로 바꿔서 서버에 보낸다. 그래서
Agent 의 초안 모델이나 프론트의 변환이 바뀌어 서버와 어긋나면 여기서 깨진다.

확인하는 것:
    ① 초안 → 등록 · 수정이 통과한다
    ② 응답의 `notice_times` 가 **초안이 보여 준 시각과 같다** — 보호자가 카드에서 승인하는 값이다
    ③ 사전에서 빠진 옛 증상 코드를 되돌려 보내는 수정이 통과한다 (core/symptom_codes.py)
    ④ `dose_date ∈ [starts_on, ends_on]` 불변식이 등록 · 수정 뒤에도 지켜진다
       (medication-tables-v1.md §5)

`fe_submit_body` 는 프론트 `submitBodyOf` 를 그대로 옮긴 것이다. 프론트가 바꾸면 여기도 맞춘다.
"""

from datetime import time, timedelta

import pytest
from sqlalchemy import select

from app.core.medication_draft import (
    DoseDraft,
    MedicationDraft,
    MedicationSnapshot,
    ScheduleDraft,
    scheduled_time_of,
)
from app.core.symptom_codes import symptom_code_set
from app.domains.medication.models import MedicationSchedule
from app.domains.medication.repository import list_doses

from .helpers import TODAY, patch, post, seed_course

CODE = sorted(symptom_code_set())[0]
RETIRED = "retired_code_from_old_dictionary"


def dose_draft(anchor: str, offset: int = 30, fixed: str | None = None) -> DoseDraft:
    fixed_time = time.fromisoformat(fixed) if fixed else None
    return DoseDraft(
        timing_anchor=anchor,
        offset_minutes=offset,
        fixed_time=fixed_time,
        scheduled_time=scheduled_time_of(anchor, offset, fixed_time),
    )


def schedule_draft(**over) -> ScheduleDraft:
    values = {
        "title": "아목시실린",
        "dosage": "5ml",
        "indication_text": "중이염",
        "storage": None,
        "med_form": None,
        "note": None,
        "symptom_codes": [CODE],
        "starts_on": TODAY + timedelta(days=1),
        "ends_on": TODAY + timedelta(days=3),
    }
    return ScheduleDraft(**values | over)


def make_draft(doses, *, op="create", schedule_id=None, **schedule) -> MedicationDraft:
    before = None
    if op == "update":
        before = MedicationSnapshot(schedule=schedule_draft(), doses=[dose_draft("breakfast")])
    return MedicationDraft(
        draft_id="draft-1",
        op=op,
        source=None,  # 처방전 경로가 생기기 전의 Agent 초안은 늘 null 이다
        schedule=schedule_draft(**schedule),
        doses=doses,
        notice_times=sorted(f"{d.scheduled_time:%H:%M}" for d in doses),
        missing=[],
        schedule_id=schedule_id,
        before=before,
    )


def fe_submit_body(draft: MedicationDraft, *, with_source: bool) -> dict:
    """프론트 `submitBodyOf` (lib/medication.ts) 를 그대로 옮긴 것."""
    payload = draft.to_payload()
    s = payload["schedule"]

    def trimmed_or_null(value):
        return None if value is None or value.strip() == "" else value.strip()

    body = {
        "schedule": {
            "title": s["title"].strip(),
            "dosage": trimmed_or_null(s["dosage"]),
            "indication_text": s["indication_text"],
            "storage": s["storage"],
            "med_form": s["med_form"],
            "note": trimmed_or_null(s["note"]),
            "symptom_codes": s["symptom_codes"],
            "starts_on": s["starts_on"],
            "ends_on": s["ends_on"],
        },
        "doses": [
            {key: d[key] for key in ("timing_anchor", "offset_minutes", "fixed_time")}
            for d in payload["doses"]
        ],
    }
    if with_source:
        body["source"] = payload["source"] or "utterance"  # 프론트: draft.source ?? "utterance"
    return body


PATTERNS = {
    "식후 30분 세 번": [dose_draft("breakfast"), dose_draft("lunch"), dose_draft("dinner")],
    "기상 -120 · 취침 +180 (기준 경계)": [dose_draft("wake_up", -120), dose_draft("bedtime", 180)],
    "fixed 시각": [dose_draft("fixed", 0, "21:00")],
    "식전 30분": [dose_draft("lunch", -30), dose_draft("dinner", -30)],
    "취침 +180 (가장 늦은 알림)": [dose_draft("bedtime", 180)],
}


@pytest.mark.parametrize("name", list(PATTERNS))
async def test_Agent_초안을_프론트_규칙으로_바꿔_등록하면_초안이_보여_준_시각_그대로_저장된다(
    db_client, bearer, consented, name
):
    draft = make_draft(PATTERNS[name])

    response = await post(
        db_client,
        bearer[0],
        consented,
        fe_submit_body(draft, with_source=True),
        key=f"roundtrip-{list(PATTERNS).index(name)}",
    )

    assert response.status_code == 201
    # 보호자가 카드에서 승인한 시각 == 서버가 저장하고 알려 주는 시각
    assert response.json()["course"]["notice_times"] == draft.notice_times


async def test_Agent_수정_초안을_프론트_규칙으로_바꿔_수정하면_초안의_시각_그대로다(
    db_client, bearer, consented
):
    first = make_draft(PATTERNS["식후 30분 세 번"])
    created = (
        await post(db_client, bearer[0], consented, fe_submit_body(first, with_source=True))
    ).json()["course"]
    changed = [dose_draft("breakfast"), dose_draft("lunch"), dose_draft("fixed", 0, "20:00")]
    update = make_draft(changed, op="update", schedule_id=created["id"])

    response = await patch(
        db_client,
        bearer[0],
        consented,
        created["id"],
        fe_submit_body(update, with_source=False),
    )

    assert response.status_code == 200
    assert response.json()["course"]["notice_times"] == update.notice_times


async def test_프론트가_초안의_보관_제형_메모를_채워_보내도_통과한다(db_client, bearer, consented):
    # 카드에서 보호자가 고르는 선택 칸 — Agent 는 null 로 내고, 보호자가 채운 값이 본문으로 간다
    draft = make_draft(
        PATTERNS["식후 30분 세 번"], storage="fridge", med_form="liquid", note=" 식후 "
    )

    response = await post(db_client, bearer[0], consented, fe_submit_body(draft, with_source=True))

    assert response.status_code == 201


async def test_사전에서_빠진_옛_증상_코드를_되돌려_보내는_수정은_통과한다(
    db_client, session, bearer, consented
):
    # 화면은 증상 코드를 고치지 않고 초안 값을 되돌려 보낸다 — 거르면 그 코스를 못 고친다
    course = await seed_course(session, consented, starts=-1, ends=1)
    course.symptom_codes = [RETIRED]
    await session.flush()
    update = make_draft(
        PATTERNS["식후 30분 세 번"],
        op="update",
        schedule_id=str(course.id),
        symptom_codes=[RETIRED],
        starts_on=TODAY - timedelta(days=1),
        ends_on=TODAY + timedelta(days=1),
    )

    response = await patch(
        db_client, bearer[0], consented, course.id, fe_submit_body(update, with_source=False)
    )

    assert response.status_code == 200


async def stored_outside_period(session, course_id) -> list:
    course = await session.scalar(
        select(MedicationSchedule)
        .where(MedicationSchedule.id == course_id)
        .execution_options(populate_existing=True)
    )
    doses = await list_doses(session, schedule_ids=[course_id])
    return [d.dose_date for d in doses if not course.starts_on <= d.dose_date <= course.ends_on]


async def test_등록하면_모든_회차가_기간_안에_있다_시작이_과거여도(
    db_client, session, bearer, consented
):
    draft = make_draft(
        PATTERNS["식후 30분 세 번"],
        starts_on=TODAY - timedelta(days=2),
        ends_on=TODAY + timedelta(days=1),
    )

    created = (
        await post(db_client, bearer[0], consented, fe_submit_body(draft, with_source=True))
    ).json()["course"]

    assert await stored_outside_period(session, created["id"]) == []


async def test_시간이_흐른_코스의_시작일을_늦추면_기간_밖_회차를_남기지_않고_거절한다(
    db_client, session, bearer, consented
):
    course = await seed_course(
        session, consented, starts=-2, ends=1
    )  # 그제 · 어제 회차가 이미 있다
    update = make_draft(
        PATTERNS["식후 30분 세 번"],
        op="update",
        schedule_id=str(course.id),
        symptom_codes=[],
        starts_on=TODAY,
        ends_on=TODAY + timedelta(days=1),
    )

    response = await patch(
        db_client, bearer[0], consented, course.id, fe_submit_body(update, with_source=False)
    )

    assert response.status_code == 400
    assert response.json()["error"]["detail"] == {
        "fields": ["schedule.starts_on", "schedule.ends_on"]
    }
    assert await stored_outside_period(session, course.id) == []  # 아무것도 바뀌지 않았다
