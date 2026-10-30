"""복약 — `POST /children/{cid}/medications` (#577).

🚨 코스 등록은 승인 게이트 ㉡ 이다 — 보호자가 복약 카드에서 확인한 것만 저장한다.
   Agent 는 초안까지만 만들고 코스도 회차도 직접 쓰지 않는다. Idempotency-Key 가 필수다.
🚨 이 라우터는 HTTP 만 안다. 칸 검증 · 중복 · 회차 펼치기는 `domains/medication/service.py` 가 한다.
"""

from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.medications import (
    MedicationCourseOut,
    MedicationDoseOut,
    SubmitMedicationRequest,
    SubmitMedicationResponse,
)
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import latest_action
from app.domains.medication.models import MedicationSchedule
from app.domains.medication.repository import list_doses
from app.domains.medication.service import (
    CourseAlreadyExists,
    CourseInput,
    CourseInvalid,
    DosePattern,
    create_course,
)

router = APIRouter()

KST = ZoneInfo("Asia/Seoul")


def kst_today() -> date:
    """서버의 한국 날짜. 화면은 오늘을 따로 구하지 않는다. 테스트가 이 함수를 바꿔 끼운다."""
    return datetime.now(KST).date()


async def _course_out(session, course: MedicationSchedule, today: date) -> MedicationCourseOut:
    doses = await list_doses(session, schedule_ids=[course.id])
    return MedicationCourseOut(
        id=course.id,
        title=course.title,
        dosage=course.dosage,
        starts_on=course.starts_on,
        ends_on=course.ends_on,
        notice_times=sorted({f"{d.scheduled_time:%H:%M}" for d in doses}),
        doses=[
            MedicationDoseOut(
                id=d.id,
                dose_date=d.dose_date,
                scheduled_time=d.scheduled_time,
                checked_at=d.checked_at,
                checkable=True,  # 오늘까지의 회차만 싣는다 — 싣는 것은 모두 지금 체크할 수 있다
            )
            for d in doses
            if d.dose_date <= today
        ],
    )


@router.post(
    "/children/{cid}/medications",
    status_code=201,
    summary="복약 코스 등록 (승인 게이트 ㉡)",
    # JSONResponse 를 돌려줘도 Swagger 에 201 의 모양이 나오게 (health_safety.py 와 같다)
    response_model=SubmitMedicationResponse,
    responses={
        400: {
            "model": ErrorEnvelope,
            "description": (
                "`validation_failed` — 본문 형식이 틀렸거나(모르는 칸 · 잘못된 값) 내용이 "
                "틀렸다(빈 약 "
                "이름 · 길이 초과 · 기간 90일 초과 · 이미 지난 기간 · 회차 0개 또는 25개 이상 · "
                "같은 알림 "
                "시각 · 사전에 없는 증상 코드). `error.detail.fields` 에 틀린 칸 이름이 온다 "
                '(예: `["schedule.title"]`, `["doses"]`). '
                "또는 `idempotency_key_required` — `Idempotency-Key` 헤더가 없다"
            ),
        },
        403: {
            "model": ErrorEnvelope,
            "description": (
                "`child_access_denied` — 내 아이가 아니다. 또는 `consent_required` — 아이의 "
                "건강정보 "
                "동의(`child_health`)가 없다"
            ),
        },
        409: {
            "model": ErrorEnvelope,
            "description": (
                "`already_exists` — 같은 아이 · 같은 약 이름 · 같은 시작일과 끝일의 진행 중인 "
                "코스가 "
                "이미 있다. 멈춘 코스는 세지 않는다"
            ),
        },
    },
)
async def register_medication(
    child: AccessibleChild,
    body: SubmitMedicationRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(
            description=(
                "**필수.** 요청 1건을 가리키는 키(화면이 만든다). 같은 키로 다시 보내면 코스를 "
                "또 만들지 "
                "않고 처음 응답(201)을 그대로 돌려준다. 키가 같고 본문이 다른 요청은 지금 막지 "
                "않는다"
            ),
        ),
    ] = None,
) -> JSONResponse:
    """보호자가 확인한 복약 코스 1건을 등록하고, 기간의 날마다 회차를 만든다.

    **코스**는 약 1종의 복용 계획이다. 약이 3종이면 코스 3건을 따로 등록한다. `doses` 는 날짜 없는
    하루 시각 패턴이고, 서버가 `starts_on` ~ `ends_on` 의 날마다 펼쳐 **회차**를 만든다
    (3일 × 하루 3번 = 9행). 체크한 기록이 곧 복용 기록이다.

    - **알림 시각**은 서버가 `timing_anchor` 의 기준 시각 + `offset_minutes` 로 계산한다. 응답의
      `notice_times` 가 실제로 울릴 시각이다.
    - **지난 날짜의 회차는 만들지 않는다.** 시작일이 과거여도 등록할 수 있고, 오늘 회차는 이미 지난
      시각이어도 만든다. 기간 전체가 이미 지났으면 400 이다.
    - 응답의 `course.doses` 는 **오늘까지**의 회차만 싣는다. 내일부터 먹는 약은 빈 목록이다.
    - 약 이름 · 칸 값은 **보낸 그대로** 저장한다(빈 문자열인 선택 칸만 null).

    오류 (순서대로): 403 `child_access_denied` → 400 `validation_failed`(본문 형식) →
    400 `idempotency_key_required` → (같은 키면 처음 응답 재생) → 403 `consent_required` →
    400 `validation_failed`(내용) → 409 `already_exists`. 앞에서 걸리면 아무것도 저장하지 않는다.

    진행 중인 코스 조회와 회차 체크는 이 API 에 없다. 코스를 멈추는 것은 대화에서 한 번 되물은 뒤
    Agent 가 처리해서 화면에서 부르는 중단 API 도 없다.
    """
    # 검사 순서: 의존성(아이 접근 · 본문 형식) → 키 → 재생 → 동의 → 내용 → 중복 → 저장 → commit →
    # 응답 기억. 알레르기 등록(health_safety.py)과 같은 순서다
    if not idempotency_key:
        raise ApiError(
            400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
        )
    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if isinstance(replayed, dict):
        return JSONResponse(status_code=201, content=replayed)

    # 민감정보는 동의 없이 저장하지 않는다 (개인정보보호법 제23조)
    granted = await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.child_id)
    if granted is not ConsentAction.GRANTED:
        raise ApiError(
            403,
            "consent_required",
            "건강 정보 동의가 필요해요",
            {"scopes": [ConsentScope.CHILD_HEALTH.value]},
        )

    today = kst_today()
    try:
        course = await create_course(
            session,
            child_id=child.child_id,
            data=CourseInput(
                title=body.schedule.title,
                starts_on=body.schedule.starts_on,
                ends_on=body.schedule.ends_on,
                source=body.source,
                doses=[
                    DosePattern(d.timing_anchor, d.offset_minutes, d.fixed_time) for d in body.doses
                ],
                dosage=body.schedule.dosage,
                indication_text=body.schedule.indication_text,
                symptom_codes=body.schedule.symptom_codes,
                storage=body.schedule.storage,
                med_form=body.schedule.med_form,
                note=body.schedule.note,
            ),
            today=today,
        )
    except CourseInvalid as exc:
        raise ApiError(
            400, "validation_failed", "입력한 복약 정보를 확인해 주세요", {"fields": exc.fields}
        ) from exc
    except CourseAlreadyExists as exc:
        raise ApiError(409, "already_exists", "이미 등록된 복약이에요") from exc

    response = SubmitMedicationResponse(
        course=await _course_out(session, course, today)
    ).model_dump(mode="json")
    await session.commit()
    idempotency.remember(**scope, replay=response)
    return JSONResponse(status_code=201, content=response)
