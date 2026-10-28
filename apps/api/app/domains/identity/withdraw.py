"""계정 탈퇴 — 유예 없이 바로 지운다 (#379 · 노션 삭제 정책 정본 §9 · #167).

호출자의 트랜잭션 안에서 돈다 — 여기서 commit 하지 않는다. 하나라도 실패하면 전부 되돌아간다.

- 등록 보호자: 아이를 먼저 지운다(purge_child) → 보호자를 지운다(purge_parent).
  child.owner_parent_id 에 ON DELETE 가 없어서 순서를 바꾸면 DB 가 막는다. 아이가 지워지면
  함께 보던 보호자의 연결 행도 CASCADE 로 지워진다. 넘기기(이관)는 첫 배포에 없다.
- 함께 보는 보호자: 보호자만 지운다. 공동 기록의 "적은 사람" 은 SET NULL 로 비워진다.

보호자 행을 지우면 세션 · 로그인 연결 · 기기 · 일기 · 개인 알림 · 연결 행 · 앱 안 신고가 CASCADE 로
따라온다.
동의 증빙 보관은 purge_* 가 한다.

🚨 사진 원본 같은 외부 파일은 지우지 않는다. 지금은 파일 저장이 없고, 생기면 그 묶음(K)이 맡는다.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child
from app.domains.consent.retention import purge_child, purge_parent
from app.domains.identity.models import AuthHandoff, AuthIdentity, AuthProvider, Parent


@dataclass(frozen=True)
class Withdrawn:
    kakao_user_id: str | None
    """커밋 뒤 카카오 연결을 끊는 데 쓴다.

    커밋하면 auth_identity 와 함께 지워져서 미리 읽어 둔다.
    """
    child_ids: tuple[uuid.UUID, ...]
    """함께 지운 아이 — 등록 보호자일 때만 있다.

    커밋 뒤 메모리 보관함에서 그 아이 것을 지운다 (#561).
    """


async def withdraw_parent(
    session: AsyncSession, *, parent_id: uuid.UUID, now: datetime
) -> Withdrawn | None:
    """보호자와 그가 등록한 아이를 지운다. 보호자가 이미 없으면 None.

    잠그는 순서가 계약이다 — 로그인 대기표(auth_handoff) → 보호자 → 등록한 아이.

    🚨 대기표를 보호자보다 먼저 잠근다. 기존 회원의 로그인 교환은 대기표를 지운 뒤 세션을 만들면서
       보호자 행을 참조한다(대기표 → 보호자). 탈퇴가 보호자를 먼저 잡으면 CASCADE 로 대기표를
       지우려다 서로 기다려 교착이 된다. 같은 순서로 잡으면 한쪽이 끝날 때까지 기다릴 뿐이다.
    🚨 보호자 행을 잠근다. purge_* 는 동의 증빙을 "읽기 → 복사 → 삭제" 로 처리해서, 같은
       보호자의 탈퇴가 동시에 둘 돌면 증빙이 두 벌 복사된다. 나중 요청은 앞 요청이 커밋할
       때까지 여기서 기다렸다가, 행이 사라진 것을 보고 None 으로 끝난다.
       계정 동의를 새로 남기는 요청도 이 잠금에 막힌다 — 보관 목록을 읽은 뒤 끼어들 수 없다.
    🚨 아이 행도 보관을 읽기 전에 잠근다. 아이 동의는 함께 보는 보호자도 남길 수 있어서,
       잠그지 않으면 보관 목록을 읽은 뒤 들어온 동의가 보관 없이 함께 지워진다.
    """
    await session.execute(
        select(AuthHandoff.id).where(AuthHandoff.parent_id == parent_id).with_for_update()
    )
    locked = await session.scalar(select(Parent.id).where(Parent.id == parent_id).with_for_update())
    if locked is None:
        return None

    kakao_user_id = await session.scalar(
        select(AuthIdentity.provider_user_id).where(
            AuthIdentity.parent_id == parent_id, AuthIdentity.provider == AuthProvider.KAKAO
        )
    )
    owned = select(Child.id).where(Child.owner_parent_id == parent_id).with_for_update()
    child_ids = tuple((await session.scalars(owned)).all())
    for child_id in child_ids:
        await purge_child(session, child_id=child_id, now=now)
    await purge_parent(session, parent_id=parent_id, now=now)
    return Withdrawn(kakao_user_id=kakao_user_id, child_ids=child_ids)
