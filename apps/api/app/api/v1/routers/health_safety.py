"""알레르기 · 건강 기록 — `GET` · `POST /children/{cid}/health-safety` · `DELETE …/{id}` (E 묶음).

🚨 LLM 이 만들거나 고치지 않는다 (루트 CLAUDE.md §2). 이 라우터가 health_safety 의 **유일한 쓰기
   경로**이고 보호자 토큰으로만 열린다. 등록은 승인 게이트 ㉡ 이라 Idempotency-Key 가 필수다.
🚨 내리기는 지우지 않는다 — status 를 retracted 로 바꾼다. 안전 필터는 active 만 읽는다.
고치기(PATCH)는 #87 결정 뒤에 더한다.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope, constraint_name
from app.api.v1.schemas.health_safety import (
    CreateHealthSafetyRequest,
    CreateHealthSafetyResponse,
    HealthSafetyListResponse,
    HealthSafetyOut,
    UpdateHealthSafetyRequest,
    UpdateHealthSafetyResponse,
    WrittenBy,
)
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import latest_action
from app.domains.identity.repository import find_parent
from app.domains.safety.models import ALLERGY_SEVERITIES, HealthSafety, SafetyKind
from app.domains.safety.repository import (
    create_safety,
    find_active_safety_for_update,
    has_active_safety,
    list_active_safety_with_writer,
    retract_safety,
    revise_safety,
)

router = APIRouter()


def _out(row: HealthSafety, nickname: str | None) -> HealthSafetyOut:
    return HealthSafetyOut(
        id=row.id,
        type=row.kind,
        label=row.label,
        severity=row.severity,
        reactions=list(row.reactions),
        management=row.management,
        notes=row.notes,
        # 이름이 없으면 통째로 null — 화면이 "null님이 확인" 을 그리지 않게
        created_by=None
        if row.created_by is None or nickname is None
        else WrittenBy(parent_id=row.created_by, nickname=nickname),
        updated_at=row.updated_at,
    )


@router.get("/children/{cid}/health-safety", responses={403: {"model": ErrorEnvelope}})
async def read_health_safety(
    child: AccessibleChild, session: SessionDep
) -> HealthSafetyListResponse:
    """지금 고려 대상(active)인 알레르기 · 건강 기록. 내린 기록은 빠진다."""
    # 건강정보 동의(child_health)를 여기서는 보지 않는다. 필수 동의라 설정에서 끌 수 없어서
    # (policy/catalog.py SCOPE_CATALOG), 동의 없는 아이에게 기록이 있을 수 없다.
    # 🚨 끌 수 있게 바뀌면 등록(POST)처럼 여기서도 403 consent_required 를 확인할 것.
    rows = await list_active_safety_with_writer(session, child_id=child.child_id)
    return HealthSafetyListResponse(
        items=[_out(row, nickname) for row, nickname in rows],
        updated_at=max((row.updated_at for row, _ in rows), default=datetime.now(UTC)),
    )


@router.post(
    "/children/{cid}/health-safety",
    status_code=201,
    # JSONResponse 를 돌려줘도 Swagger 에 201 의 모양이 나오게 (suggestions.py 의 일정 제출과 같다)
    response_model=CreateHealthSafetyResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 409)},
)
async def register_health_safety(
    child: AccessibleChild,
    body: CreateHealthSafetyRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """승인 게이트 ㉡ — 보호자가 확인한 알레르기 · 건강 기록 1건을 등록한다.

    Idempotency-Key 가 필요하다. 같은 키로 다시 오면 처음 응답(201)을 그대로 돌려준다.
    오류 (순서대로): 403 child_access_denied → 400 validation_failed(본문 형식) →
    400 idempotency_key_required → 403 consent_required(건강정보 동의 없음) →
    400 validation_failed(종류와 맞지 않는 심각도) → 409 already_exists(같은 종류 · 이름의 기록).
    """
    # 검사 순서: 의존성(아이 접근 · 본문) → 키 → 재생 → 동의 → 심각도 → 중복 → 저장 → commit →
    # 응답 기억. 심각도 400 이 중복 409 보다 먼저인 것은 목(children.ts)과 같게 맞춘 것이다.
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

    # 민감정보는 동의 없이 저장하지 않는다 (개인정보보호법 제23조). 아이 등록이 받는 필수 동의라
    # 보통은 있다 — 동의를 받기 전 만들어진 아이 같은 경우를 막는다
    granted = await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.child_id)
    if granted is not ConsentAction.GRANTED:
        raise ApiError(
            403,
            "consent_required",
            "건강 정보 동의가 필요해요",
            {"scopes": [ConsentScope.CHILD_HEALTH.value]},
        )

    if body.severity is not None and (body.type is SafetyKind.ALLERGY) != (
        body.severity in ALLERGY_SEVERITIES
    ):
        # 알레르기는 검사 등급(class_0~6), 나머지는 mild~anaphylaxis (DB CHECK 와 같은 규칙)
        raise ApiError(
            400, "validation_failed", "이 종류에는 쓸 수 없는 심각도예요", {"fields": ["severity"]}
        )
    # 같은 항목은 먼저 코드로 보고, 동시에 온 두 번째 요청은 DB 의 부분 고유 인덱스
    # (uq_health_safety_active_item)가 막는다 — 아이 등록의 uq_parent_child_parent_id 와 같은 판정
    if await has_active_safety(session, child_id=child.child_id, kind=body.type, label=body.label):
        raise ApiError(409, "already_exists", "이미 등록된 항목이에요")
    try:
        # savepoint 로 감싼다 — DB 가 거절하면 이 한 줄만 되감고 세션은 그대로 쓸 수 있다
        async with session.begin_nested():
            row = await create_safety(
                session,
                child_id=child.child_id,
                parent_id=parent.parent_id,
                kind=body.type,
                label=body.label,
                severity=body.severity,
                reactions=body.reactions,
                notes=body.notes,
            )
    except ValueError as exc:
        # 종류와 맞지 않는 심각도 (create_safety 가 DB CHECK 보다 먼저 막는다)
        raise ApiError(
            400, "validation_failed", "이 종류에는 쓸 수 없는 심각도예요", {"fields": ["severity"]}
        ) from exc
    except IntegrityError as exc:
        if constraint_name(exc) != "uq_health_safety_active_item":
            raise
        raise ApiError(409, "already_exists", "이미 등록된 항목이에요") from exc
    await session.refresh(row)
    writer = await find_parent(session, parent.parent_id)
    nickname = None if writer is None else writer.nickname
    response = CreateHealthSafetyResponse(safety=_out(row, nickname)).model_dump(mode="json")
    await session.commit()
    idempotency.remember(**scope, replay=response)
    return JSONResponse(status_code=201, content=response)


@router.patch(
    "/children/{cid}/health-safety/{safety_id}",
    # JSONResponse 를 돌려줘도 Swagger 에 200 의 모양이 나오게 (등록과 같다)
    response_model=UpdateHealthSafetyResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404)},
)
async def revise_health_safety(
    child: AccessibleChild,
    safety_id: UUID,
    body: UpdateHealthSafetyRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """승인 게이트 ㉡ — 보호자가 확인한 알레르기 · 건강 기록을 고친다 (등록과 같은 게이트).

    고치면 지금 기록은 내려가고 바뀐 값으로 새 기록이 생긴다 — 응답의 id 가 바뀐다. 바뀐 것이
    없으면 새 기록 없이 지금 기록을 200 으로 돌려준다.
    Idempotency-Key 는 보내면 쓴다. 같은 키로 다시 오면 처음 응답을 그대로 돌려준다. 키 없이 같은
    고치기를 다시 보내면 지금 기록이 이미 내려가 있어서 404 다.
    오류 (순서대로): 403 child_access_denied → 400 validation_failed(본문 형식 · 종류와 이름) →
    403 consent_required(건강정보 동의 없음) → 404 not_found(내렸거나 다른 아이의 기록) →
    400 validation_failed(종류와 맞지 않는 심각도).
    """
    # 키는 선택이다 — 등록(POST)과 달리 지금 화면이 키 없이 보내도 동작해야 한다. 내리고 새로
    # 등록하는 방식이라 같은 고치기를 다시 보내면 404 가 되므로, 화면이 키를 붙이면 재시도가
    # 안전해진다
    scope = None
    if idempotency_key:
        scope = {
            "parent_id": parent.parent_id,
            "method": request.method,
            "path": request.url.path,
            "key": idempotency_key,
        }
        replayed = idempotency.recall(**scope)
        if isinstance(replayed, dict):
            return JSONResponse(status_code=200, content=replayed)

    granted = await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.child_id)
    if granted is not ConsentAction.GRANTED:
        raise ApiError(
            403,
            "consent_required",
            "건강 정보 동의가 필요해요",
            {"scopes": [ConsentScope.CHILD_HEALTH.value]},
        )

    current = await find_active_safety_for_update(
        session, child_id=child.child_id, safety_id=safety_id
    )
    if current is None:
        raise ApiError(404, "not_found", "이미 내려간 기록이에요")

    changes = body.model_dump(exclude_unset=True)
    severity = changes.get("severity", current.severity)
    reactions = changes.get("reactions", list(current.reactions))
    notes = changes.get("notes", current.notes)
    if severity is not None and (current.kind is SafetyKind.ALLERGY) != (
        severity in ALLERGY_SEVERITIES
    ):
        raise ApiError(
            400, "validation_failed", "이 종류에는 쓸 수 없는 심각도예요", {"fields": ["severity"]}
        )

    if (severity, reactions, notes) == (current.severity, list(current.reactions), current.notes):
        # 바뀐 것이 없다 — 고치기 시트는 안 고친 칸도 지금 값으로 실어 보낸다. 이력을 늘리지 않는다
        row = current
    else:
        row = await revise_safety(
            session,
            current=current,
            parent_id=parent.parent_id,
            severity=severity,
            reactions=reactions,
            notes=notes,
        )
        await session.refresh(row)
    writer = None if row.created_by is None else await find_parent(session, row.created_by)
    nickname = None if writer is None else writer.nickname
    response = UpdateHealthSafetyResponse(safety=_out(row, nickname)).model_dump(mode="json")
    await session.commit()
    if scope is not None:
        idempotency.remember(**scope, replay=response)
    return JSONResponse(status_code=200, content=response)


@router.delete(
    "/children/{cid}/health-safety/{safety_id}",
    status_code=204,
    responses={status: {"model": ErrorEnvelope} for status in (403, 404)},
)
async def retract_health_safety(
    child: AccessibleChild, safety_id: UUID, parent: CurrentParent, session: SessionDep
) -> Response:
    """기록을 내린다 — 지우지 않고 retracted 로. 이미 내렸거나 다른 아이의 기록이면 404.

    되돌릴 수 있고 같은 요청을 두 번 보내도 결과가 같아서 Idempotency-Key 를 받지 않는다.
    """
    retracted = await retract_safety(
        session, child_id=child.child_id, safety_id=safety_id, parent_id=parent.parent_id
    )
    if retracted is None:
        raise ApiError(404, "not_found", "이미 내려간 기록이에요")
    await session.commit()
    return Response(status_code=204)
