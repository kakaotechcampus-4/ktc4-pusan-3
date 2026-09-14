"""동의 저장 규칙 — 명세 docs/api/auth-kakao-v1.md §3-5 · §6-2

🚨 consent 는 append-only 다. 철회는 UPDATE/DELETE 가 아니라 withdrawn 행을 더한다
   (모델의 주석). 그래서 "지금 동의했는가" 는 마지막 행의 action 으로 판정한다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent.models import Consent, ConsentAction, ConsentScope

ACCOUNT_SCOPES: tuple[ConsentScope, ...] = (
    ConsentScope.SERVICE_TERMS,
    ConsentScope.PRIVACY_ACCOUNT,
)
"""계정 단위 필수 동의 (§6-2 ② requireAccountConsent).

아이 단위(child_basic · child_health)는 여기 없다 — 모델의 CHECK 가 그 둘은 child_id 를
요구하고, 가입 시점에는 아이가 없다.
"""


async def missing_account_scopes(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
) -> list[ConsentScope]:
    """아직 granted 가 아닌 계정 동의 스코프 (§3-4 의 consent_required).

    스코프당 마지막 행만 본다. 행 수가 스코프 수만큼이라 파이썬에서 접어도 싸고,
    DISTINCT ON 을 쓰면 이 단순한 판정에 SQL 을 읽어야 한다.
    """
    stmt = (
        select(Consent.scope, Consent.action)
        .where(Consent.parent_id == parent_id, Consent.scope.in_(ACCOUNT_SCOPES))
        .order_by(Consent.acted_at)
    )
    latest: dict[ConsentScope, ConsentAction] = {}
    for scope, action in (await session.execute(stmt)).all():
        latest[scope] = action

    return [scope for scope in ACCOUNT_SCOPES if latest.get(scope) is not ConsentAction.GRANTED]


async def grant_account_scope(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    scope: ConsentScope,
    policy_version: str,
) -> None:
    """동의 1건을 남긴다. child_id 는 없다 — 계정 스코프라 모델의 CHECK 가 NULL 을 요구한다."""
    session.add(
        Consent(
            parent_id=parent_id,
            child_id=None,
            scope=scope,
            action=ConsentAction.GRANTED,
            policy_version=policy_version,
        )
    )
    await session.flush()
