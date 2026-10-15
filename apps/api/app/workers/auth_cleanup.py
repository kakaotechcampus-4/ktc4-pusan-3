"""만료된 로그인 세션 · access · 1회용 코드를 지우는 배치 (#46 · #466)

명세 docs/api/auth-kakao-v1.md §5-3 · §5-4.

이 배치는 방어가 아니라 청소다. 만료된 값은 배치가 늦어도 통과하지 못한다 — access 판정은
session_access.expires_at 을 보고(api/deps/auth.py), 1회용 코드 소비는
`DELETE … WHERE expires_at > now()` 한 문장이다 (§5-4 "배치는 청소지 방어가 아니다").

그래도 지우는 이유는 둘이다.
  · 세션은 증빙이 아니라서 남은 행은 유출 표면일 뿐이다 (AuthSession 주석 "폐기는 행 삭제다").
  · 보관 기준(docs/safety/retention-v1.md)이 로그인 유지 값을 만료 뒤 지운다고 정했다. 그리고
    가입하다 그만둔 사람의 auth_handoff 에는 카카오 회원번호가 들어 있다 — 계정을 만들지 않은
    사람의 식별값이 기한 없이 남는다.

🚨 세션은 access 가 아니라 refresh 의 만료로 지운다 (#466). access 는 15분이라, 그 기준으로
   지우면 15분 쉰 사용자의 로그인이 이 배치에 사라진다. 만료된 access 는 따로 지운다.

부르는 곳은 `app/workers/cleanup.py` 다. 서버 cron 이 하루 한 번 돌린다 (apps/api/README.md
"만료된 값 정리").

🚨 로그에는 지운 **건수**만 남긴다. 토큰 해시 · 회원번호 · 코드는 남기지 않는다 (§7-5).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import AuthHandoff, AuthSession, SessionAccess

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PurgedAuthRows:
    sessions: int
    accesses: int
    handoffs: int


async def purge_expired_auth_rows(session: AsyncSession, *, now: datetime) -> PurgedAuthRows:
    """만료된 session · session_access · auth_handoff 행을 지우고 건수를 돌려준다.

    세션은 refresh 의 유휴 만료나 절대 만료 중 하나라도 지났으면 지운다. 딸린 access 는
    CASCADE 로 같이 사라지고, accesses 건수에는 살아 있는 세션의 만료된 access 만 든다.

    경계(`<=`)는 인증 판정과 같다 — 만료 시각과 정확히 같은 순간의 값은 이미 401 이다.
    현재 시각은 호출자가 넘긴다 (consent_purge 와 같은 이유 — 테스트가 실제 시각에 묶이지 않게).
    """
    sessions = await session.execute(
        delete(AuthSession).where(
            or_(AuthSession.refresh_expires_at <= now, AuthSession.absolute_expires_at <= now)
        )
    )
    accesses = await session.execute(delete(SessionAccess).where(SessionAccess.expires_at <= now))
    handoffs = await session.execute(delete(AuthHandoff).where(AuthHandoff.expires_at <= now))
    await session.flush()

    purged = PurgedAuthRows(
        sessions=sessions.rowcount, accesses=accesses.rowcount, handoffs=handoffs.rowcount
    )
    log.info(
        "만료된 로그인 행 정리: session %d건, session_access %d건, auth_handoff %d건",
        purged.sessions,
        purged.accesses,
        purged.handoffs,
    )
    return purged
