"""동의 저장 규칙 — 명세 docs/api/auth-kakao-v1.md §3-5 · §6-2 · 노션 정책 정본 §5

🚨 consent 는 append-only 다. 철회는 UPDATE/DELETE 가 아니라 withdrawn 행을 더한다
   (모델의 주석). 그래서 "지금 동의했는가" 는 대상+scope 의 마지막 행 action 으로
   판정한다. update/delete 함수를 두지 않는 것도 같은 이유다 — 이력을 고치는 경로가
   있으면 증빙이 증빙이 아니게 된다.

🚨 actor 는 호출자가 고르는 값이 아니다. 아래 함수들은 인증된 보호자의 id 를
   actor_parent_id 로 받고 actor_ref 를 그 값으로 직접 채운다. 클라이언트가 보낸 값을
   그대로 흘리는 인자를 만들지 않는다 (노션 Backend 공유 §2).
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
요구하고, 가입 시점에는 아이가 없다. 모델의 _ACCOUNT_SCOPE_SQL 과 같은 분류를 쓴다.
"""


async def latest_action(
    session: AsyncSession,
    *,
    scope: ConsentScope,
    subject_parent_id: uuid.UUID | None = None,
    child_id: uuid.UUID | None = None,
) -> ConsentAction | None:
    """대상+scope 의 현재 동의 상태 — 마지막 action 이 곧 현재 상태다 (정본 §5).

    별도의 현재 상태 테이블을 두지 않기로 했으므로(MVP) 이력에서 계산한다. 아직 한 번도
    동의·철회한 적이 없으면 None 이다.
    """
    stmt = (
        select(Consent.action)
        .where(Consent.scope == scope, _target_filter(subject_parent_id, child_id))
        .order_by(Consent.acted_at.desc(), Consent.id.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def missing_account_scopes(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
) -> list[ConsentScope]:
    """아직 granted 가 아닌 계정 동의 스코프 (§3-4 의 consent_required).

    계정 동의의 대상은 subject_parent_id 다 — actor 가 아니다. 남이 대신 눌러준 동의도
    그 계정의 동의로 센다.
    """
    return [
        scope
        for scope in ACCOUNT_SCOPES
        if await latest_action(session, scope=scope, subject_parent_id=parent_id)
        is not ConsentAction.GRANTED
    ]


async def record_consent(
    session: AsyncSession,
    *,
    actor_parent_id: uuid.UUID,
    scope: ConsentScope,
    action: ConsentAction,
    policy_version_id: uuid.UUID,
    subject_parent_id: uuid.UUID | None = None,
    child_id: uuid.UUID | None = None,
    guardian_attested: bool | None = None,
) -> Consent | None:
    """동의·철회 1건을 남긴다. 저장과 철회가 같은 경로이고 action 만 다르다.

    🚨 멱등 — 같은 대상+scope 의 마지막 action 이 이미 요청과 같으면 행을 넣지 않고
       None 을 돌려준다. append-only 라 재시도가 그대로 중복 이력이 되는데, 그러면
       "몇 번 동의했는가" 가 네트워크 재시도 횟수를 세게 된다 (노션 Backend §7).
       상태가 실제로 바뀌는 요청만 이력에 남는다.

    actor_ref 는 actor_parent_id 로 여기서 채운다 — 호출자가 따로 넘기지 못한다.
    """
    if await latest_action(
        session, scope=scope, subject_parent_id=subject_parent_id, child_id=child_id
    ) is action:
        return None

    consent = Consent(
        subject_parent_id=subject_parent_id,
        child_id=child_id,
        actor_parent_id=actor_parent_id,
        actor_ref=actor_parent_id,
        scope=scope,
        action=action,
        policy_version_id=policy_version_id,
        guardian_attested=guardian_attested,
    )
    session.add(consent)
    await session.flush()
    return consent


async def grant_account_scope(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    scope: ConsentScope,
    policy_version_id: uuid.UUID,
) -> Consent | None:
    """가입 시점의 계정 동의 1건. 본인이 본인 계정에 동의하므로 actor 와 subject 가 같다."""
    return await record_consent(
        session,
        actor_parent_id=parent_id,
        subject_parent_id=parent_id,
        scope=scope,
        action=ConsentAction.GRANTED,
        policy_version_id=policy_version_id,
    )


def _target_filter(subject_parent_id: uuid.UUID | None, child_id: uuid.UUID | None):
    """동의 대상 하나를 가리키는 WHERE 조각.

    대상은 정확히 하나여야 한다 — 모델의 CHECK 가 저장할 때 막는 것을 조회에서도 같은
    기준으로 요구한다. 둘 다 넘기거나 둘 다 빼면 "어느 대상의 상태인가" 가 정해지지
    않는데, 그대로 두면 다른 대상의 행까지 섞어 세고도 조용히 값을 돌려준다.
    """
    if (subject_parent_id is None) == (child_id is None):
        raise ValueError("동의 대상은 subject_parent_id 와 child_id 중 정확히 하나여야 한다")
    if child_id is not None:
        return Consent.child_id == child_id
    return Consent.subject_parent_id == subject_parent_id
