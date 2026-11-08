"""알레르기 · 건강 기록 — `GET` · `POST /children/{cid}/health-safety` · `PATCH` · `DELETE …/{id}`.

🚨 LLM 이 만들거나 고치지 않는다 (루트 CLAUDE.md §2). 이 라우터가 health_safety 의 **유일한 쓰기
   경로**이고 보호자 토큰으로만 열린다. 등록 · 고치기는 승인 게이트 ㉡ 이라 Idempotency-Key 가
   필수다.
🚨 내리기는 지우지 않는다 — status 를 retracted 로 바꾼다. 안전 필터는 active 만 읽는다.
🚨 괜찮았던 식품(`tolerated` · status none)은 알레르기가 아니다 — 목록에서 `items` 와 따로 싣고,
   같은 식품이 알레르기로 있으면 받지 않는다. 알레르기를 등록하면 그것이 덮는 괜찮았던 식품을
   내린다(알레르기가 이긴다). "덮는다" 는 05 사전검사와 같은 이름 사전으로 본다.
🚨 고치기도 지우지 않는다 — 지금 기록을 내리고 새 기록을 넣는다 (#87, 이력이 남는다).
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.agents import entrypoint
from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope, constraint_name
from app.api.v1.health_consent import health_consent_required
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
from app.domains.safety.labels import list_safety_labels
from app.domains.safety.models import ALLERGY_SEVERITIES, HealthSafety, SafetyKind, SafetyStatus
from app.domains.safety.repository import (
    create_safety,
    find_active_safety_for_update,
    has_active_safety,
    has_tolerated_safety,
    list_active_safety_with_writer,
    list_tolerated_safety_with_writer,
    retract_safety,
    retract_tolerated,
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
    """알레르기 · 건강 기록(items)과 괜찮았던 식품(tolerated). 내린 기록은 빠진다."""
    # 건강정보 동의(child_health)를 여기서는 보지 않는다. 필수 동의라 설정에서 끌 수 없어서
    # (policy/catalog.py SCOPE_CATALOG), 동의 없는 아이에게 기록이 있을 수 없다.
    # 🚨 끌 수 있게 바뀌면 등록(POST)처럼 여기서도 403 consent_required 를 확인할 것.
    rows = await list_active_safety_with_writer(session, child_id=child.child_id)
    tolerated = await list_tolerated_safety_with_writer(session, child_id=child.child_id)
    return HealthSafetyListResponse(
        items=[_out(row, nickname) for row, nickname in rows],
        tolerated=[_out(row, nickname) for row, nickname in tolerated],
        updated_at=max(
            (row.updated_at for row, _ in (*rows, *tolerated)), default=datetime.now(UTC)
        ),
    )


@router.post(
    "/children/{cid}/health-safety",
    status_code=201,
    # JSONResponse 를 돌려줘도 Swagger 에 201 의 모양이 나오게 (suggestions.py 의 일정 제출과 같다)
    response_model=CreateHealthSafetyResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 409, 503)},
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

    괜찮았던 식품(`tolerated: true`)은 동의 뒤 순서가 이렇다: 400 validation_failed(알레르기가
    아니거나 심각도 · 증상을 같이 보냄) → 409 allergy_exists(그 식품이 알레르기로 있음 · 이름
    사전) → 409 already_exists(같은 이름의 괜찮았던 식품).
    알레르기를 등록하면 그 알레르기가 덮는 괜찮았던 식품을 같은 트랜잭션에서 내린다.
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
    replayed = await idempotency.recall(**scope)
    if isinstance(replayed, dict):
        return JSONResponse(status_code=201, content=replayed)

    # 민감정보는 동의 없이 저장하지 않는다 (개인정보보호법 제23조). 아이 등록이 받는 필수 동의라
    # 보통은 있다 — 동의를 받기 전 만들어진 아이 같은 경우를 막는다
    granted = await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.child_id)
    if granted is not ConsentAction.GRANTED:
        raise health_consent_required()

    if body.tolerated:
        row = await _register_tolerated(session, body=body, child=child, parent=parent)
        return await _created(session, row=row, parent=parent, scope=scope)

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
    covered = await _tolerated_covered_by(
        session, child_id=child.child_id, kind=body.type, label=body.label
    )
    try:
        # savepoint 로 감싼다 — DB 가 거절하면 이 한 줄만 되감고 세션은 그대로 쓸 수 있다
        async with session.begin_nested():
            # 알레르기가 이긴다 — 이 알레르기가 덮는 괜찮았던 식품을 같이 내린다
            await retract_tolerated(
                session, child_id=child.child_id, ids=covered, parent_id=parent.parent_id
            )
            row = await create_safety(
                session,
                child_id=child.child_id,
                parent_id=parent.parent_id,
                kind=body.type,
                label=body.label,
                severity=body.severity,
                reactions=body.reactions,
                notes=body.notes or None,  # 빈 메모는 null 로
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
    return await _created(session, row=row, parent=parent, scope=scope)


async def _created(session, *, row: HealthSafety, parent, scope: dict) -> JSONResponse:
    """등록 201 — 커밋한 뒤 응답을 키에 기억한다(같은 키는 처음 응답을 재생)."""
    await session.refresh(row)
    writer = await find_parent(session, parent.parent_id)
    nickname = None if writer is None else writer.nickname
    response = CreateHealthSafetyResponse(safety=_out(row, nickname)).model_dump(mode="json")
    await session.commit()
    await idempotency.remember(**scope, replay=response)
    return JSONResponse(status_code=201, content=response)


async def _safety_labels(session, *, child_id: UUID) -> list[entrypoint.SafetyLabel]:
    """05 사전검사가 읽는 것과 같은 행(모든 kind · status) — 같은 이름 사전으로 맞추려고 읽는다."""
    rows = await list_safety_labels(session, child_id=child_id)
    return [entrypoint.SafetyLabel(*r) for r in rows]


async def _tolerated_covered_by(
    session, *, child_id: UUID, kind: SafetyKind, label: str
) -> list[UUID]:
    """새 기록이 알레르기 · 건강 기록(active)으로 들어오면 덮게 되는 괜찮았던 식품의 id.

    사전검사와 같은 판정이다 — 새 기록 하나만 active 로 두고 괜찮았던 식품 이름이 걸리는지 본다.
    """
    tolerated = await list_tolerated_safety_with_writer(session, child_id=child_id)
    if not tolerated:
        return []
    incoming = [entrypoint.SafetyLabel(kind.value, label, SafetyStatus.ACTIVE.value)]
    return [
        row.id for row, _ in tolerated if entrypoint.check_allergens([row.label], incoming).blocked
    ]


async def _register_tolerated(
    session, *, body: CreateHealthSafetyRequest, child, parent
) -> HealthSafety:
    """괜찮았던 식품 1건 — 05 확인 시트에서 "먹어봤어요 · 괜찮았어요" 로 답한 식품.

    🚨 알레르기가 아니다. 같은 식품이 알레르기로 있으면 받지 않는다 — 프로필의 두 목록이 서로
       반대말을 하면 안 된다. 판정은 05 사전검사와 같은 이름 사전이다("견과류" 알레르기가 있으면
       "호두" 를 받지 않는다).
    """
    wrong = [
        field
        for field, bad in (
            ("type", body.type is not SafetyKind.ALLERGY),
            ("severity", body.severity is not None),
            ("reactions", bool(body.reactions)),
        )
        if bad
    ]
    if wrong:
        raise ApiError(
            400,
            "validation_failed",
            "괜찮았던 식품은 알레르기 항목 이름만 받아요",
            {"fields": wrong},
        )
    labels = await _safety_labels(session, child_id=child.child_id)
    if entrypoint.check_allergens([body.label], labels).blocked:
        raise ApiError(409, "allergy_exists", "알레르기로 등록된 식품이에요")
    if await has_tolerated_safety(session, child_id=child.child_id, label=body.label):
        raise ApiError(409, "already_exists", "이미 등록된 항목이에요")
    try:
        async with session.begin_nested():
            return await create_safety(
                session,
                child_id=child.child_id,
                parent_id=parent.parent_id,
                kind=SafetyKind.ALLERGY,
                label=body.label,
                notes=body.notes or None,
                status=SafetyStatus.NONE,
            )
    except IntegrityError as exc:
        # 동시에 온 두 번째 요청 — 부분 고유 인덱스가 막는다
        if constraint_name(exc) != "uq_health_safety_none_item":
            raise
        raise ApiError(409, "already_exists", "이미 등록된 항목이에요") from exc


@router.patch(
    "/children/{cid}/health-safety/{safety_id}",
    # JSONResponse 를 돌려줘도 Swagger 에 200 의 모양이 나오게 (등록과 같다)
    response_model=UpdateHealthSafetyResponse,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404, 503)},
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
    Idempotency-Key 가 필요하다 (등록과 같다). 같은 키로 다시 오면 처음 응답을 그대로 돌려준다 —
    고친 뒤에는 옛 기록이 내려가 있어서, 다른 키로 같은 고치기를 다시 보내면 404 다.
    오류 (순서대로): 403 child_access_denied → 400 validation_failed(본문 형식 · 종류와 이름) →
    400 idempotency_key_required → 403 consent_required(건강정보 동의 없음) →
    404 not_found(내렸거나 다른 아이의 기록) → 400 validation_failed(종류와 맞지 않는 심각도).
    """
    # 키는 필수다 (#370 리뷰). 내리고 새로 등록하는 방식이라 응답을 잃고 다시 보낸 고치기는
    # 404 가 된다 — 같은 키면 처음 응답을 재생해서 그 길을 막는다. 화면은 #386 부터 늘 붙인다
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
    replayed = await idempotency.recall(**scope)
    if isinstance(replayed, dict):
        return JSONResponse(status_code=200, content=replayed)

    granted = await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.child_id)
    if granted is not ConsentAction.GRANTED:
        raise health_consent_required()

    current = await find_active_safety_for_update(
        session, child_id=child.child_id, safety_id=safety_id
    )
    if current is None:
        raise ApiError(404, "not_found", "이미 내려간 기록이에요")

    changes = body.model_dump(exclude_unset=True)
    severity = changes.get("severity", current.severity)
    reactions = changes.get("reactions", list(current.reactions))
    notes = changes.get("notes", current.notes) or None  # 빈 메모는 null 로
    if severity is not None and (current.kind is SafetyKind.ALLERGY) != (
        severity in ALLERGY_SEVERITIES
    ):
        raise ApiError(
            400, "validation_failed", "이 종류에는 쓸 수 없는 심각도예요", {"fields": ["severity"]}
        )

    unchanged = (current.severity, list(current.reactions), current.notes or None)
    if (severity, reactions, notes) == unchanged:
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
    await idempotency.remember(**scope, replay=response)
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

    알레르기 · 건강 기록과 괜찮았던 식품을 같은 길로 내린다. 괜찮았던 식품을 내리면 다음 05
    사전검사에서 그 식품을 다시 묻는다.

    되돌릴 수 있고 같은 요청을 두 번 보내도 결과가 같아서 Idempotency-Key 를 받지 않는다.
    """
    retracted = await retract_safety(
        session, child_id=child.child_id, safety_id=safety_id, parent_id=parent.parent_id
    )
    if retracted is None:
        raise ApiError(404, "not_found", "이미 내려간 기록이에요")
    await session.commit()
    return Response(status_code=204)
