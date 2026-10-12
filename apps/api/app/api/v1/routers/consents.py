"""설정 화면에서 동의 켜고 끄기 — `GET /consents` · `POST /consents` (#181).

위치기반서비스 이용약관 제10조 "설정 화면에서 언제든 끌 수 있다" 의 창구다.
화면(consent-section.tsx)은
선택 동의만 스위치로 그리고, 바꾼 뒤 GET 을 다시 불러온다.

🚨 가입 · 아이 등록과 **같은 규칙**이다 — 받는 버전은 GET /policies 가 지금 보여 주는 것 하나
   (find_active_versions). 끄기는 withdrawn 행을 더하고 이력을 고치지 않는다 (append-only).
🚨 필수 동의는 끌 수 없다. 필수를 끄는 것은 곧 탈퇴다 (#181).
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.consents import (
    ConsentHistoryEntry,
    ConsentOut,
    ConsentRequest,
    ConsentResponse,
    ConsentsResponse,
)
from app.domains.child.repository import find_accessible_child
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import (
    ACCOUNT_SCOPES,
    CHILD_SCOPES,
    REQUIRED_ACCOUNT_SCOPES,
    REQUIRED_CHILD_SCOPES,
    latest_action,
    latest_consent,
    list_consent_history,
    record_consent,
)
from app.domains.policy.repository import find_active_versions, find_version

router = APIRouter()

_REQUIRED = frozenset(REQUIRED_ACCOUNT_SCOPES + REQUIRED_CHILD_SCOPES)


async def _ensure_child(session: AsyncSession, *, child_id: UUID, parent_id: UUID) -> None:
    """그 아이에 연결된 보호자인가. 아니면 403 — 아이 주소의 AccessibleChild 와 같은 판단."""
    if await find_accessible_child(session, child_id=child_id, parent_id=parent_id) is None:
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")


async def _effective(
    session: AsyncSession, *, parent_id: UUID, child_id: UUID | None
) -> dict[str, bool]:
    """scope → 지금 동의했는가. 마지막 action 이 곧 현재 상태다 (정본 §5)."""
    effective = {
        scope.value: await latest_action(session, scope=scope, subject_parent_id=parent_id)
        is ConsentAction.GRANTED
        for scope in ACCOUNT_SCOPES
    }
    if child_id is not None:
        for scope in CHILD_SCOPES:
            effective[scope.value] = (
                await latest_action(session, scope=scope, child_id=child_id)
                is ConsentAction.GRANTED
            )
    return effective


@router.get("/consents", responses={403: {"model": ErrorEnvelope}})
async def read_consents(
    parent: CurrentParent, session: SessionDep, child_id: UUID | None = None
) -> ConsentsResponse:
    """내 계정 동의 현황과, child_id 를 주면 그 아이의 동의 현황. 남의 아이면 403."""
    if child_id is not None:
        await _ensure_child(session, child_id=child_id, parent_id=parent.parent_id)
    rows = await list_consent_history(session, parent_id=parent.parent_id, child_id=child_id)
    return ConsentsResponse(
        effective=await _effective(session, parent_id=parent.parent_id, child_id=child_id),
        history=[
            ConsentHistoryEntry(
                scope=row.scope.value,
                action=row.action,
                policy_version=version,
                acted_at=row.acted_at,
            )
            for row, version in rows
        ],
    )


@router.post(
    "/consents",
    status_code=201,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def change_consent(
    body: ConsentRequest, parent: CurrentParent, session: SessionDep
) -> ConsentResponse:
    """동의 1건을 켜거나 끈다. 같은 상태로 다시 오면 기록을 더하지 않고 마지막 행을 돌려준다.

    다만 새 약관 버전에 다시 동의하면 새 행을 남긴다 — 새 글에 동의한 것은 새 증빙이다.

    오류 (순서대로): 400 validation_failed(대상 · 필수 끄기) → 403 child_access_denied →
    403 consent_required(아이 동의의 법정대리인 확인) → 400 policy_version_invalid.
    버전 — 켜기는 GET /policies 가 지금 보여 주는 것만, 끄기는 등록된 버전이면 받는다.
    """
    scope = ConsentScope(body.scope)
    is_account = scope in ACCOUNT_SCOPES
    # 대상은 정확히 하나 — 계정 동의는 보호자 본인, 아이 동의는 그 아이 (consent 모델의 CHECK)
    if is_account == (body.child_id is not None):
        raise ApiError(
            400,
            "validation_failed",
            "동의 대상을 다시 확인해 주세요",
            {"fields": ["child_id"]},
        )
    if body.action is ConsentAction.WITHDRAWN and scope in _REQUIRED:
        raise ApiError(
            400,
            "validation_failed",
            "필수 동의는 철회할 수 없어요. 탈퇴로만 끝낼 수 있어요",
            {"fields": ["scope"]},
        )
    if not is_account:
        await _ensure_child(session, child_id=body.child_id, parent_id=parent.parent_id)
        if body.action is ConsentAction.GRANTED and body.guardian_attested is not True:
            # 동의와 법정대리인 확인은 별개 의무다 (개인정보보호법 제22조의2 ①) — 아이 등록과 같다
            raise ApiError(
                403, "consent_required", "법정대리인 확인이 필요해요", {"scopes": [scope.value]}
            )

    if body.action is ConsentAction.GRANTED:
        # 동의는 지금 보여 주는 버전만 — 보여 주지 않은 글에 "동의했다" 를 남기지 않는다 (#91)
        current = {
            row.scope: row for row in await find_active_versions(session, now=datetime.now(UTC))
        }
        registered = current.get(scope)
    else:
        # 철회는 등록된 버전이면 받는다 — 약관이 바뀌거나 끝났다고 끄는 길이 막히면 안 된다
        # (위치 약관 제10조 "언제든"). 철회 행은 화면이 보낸 그 버전을 가리킨다
        registered = await find_version(session, scope=scope, version=body.policy_version)
    if registered is None or registered.version != body.policy_version:
        raise ApiError(
            400,
            "policy_version_invalid",
            "동의 화면을 다시 불러와 주세요",
            {"scope": scope.value, "policy_version": body.policy_version},
        )

    target = (
        {"child_id": body.child_id} if not is_account else {"subject_parent_id": parent.parent_id}
    )
    created = await record_consent(
        session,
        actor_parent_id=parent.parent_id,
        scope=scope,
        action=body.action,
        policy_version_id=registered.id,
        guardian_attested=None if is_account else body.guardian_attested,
        **target,
    )
    if created is not None:
        row, version = created, registered.version
    else:
        # 이미 같은 상태다 — 기록을 더하지 않고 마지막 행을 돌려준다 (record_consent 의 멱등)
        latest = await latest_consent(session, scope=scope, **target)
        if latest is None:  # 같은 상태라는 것은 행이 있다는 뜻이라 여기 오지 않는다
            raise RuntimeError("같은 상태인데 마지막 동의 행이 없다")
        row, version = latest

    # 응답은 commit 전에 만든다 — commit 뒤에 터지면 저장됐는데 실패로 보인다 (#214 리뷰 ⑤)
    response = ConsentResponse(
        consent=ConsentOut(
            id=row.id,
            scope=row.scope.value,
            action=row.action,
            policy_version=version,
            acted_at=row.acted_at,
        ),
        effective=await _effective(session, parent_id=parent.parent_id, child_id=body.child_id),
    )
    await session.commit()
    return response
