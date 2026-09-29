"""만료된 로그인 세션 · 1회용 코드를 지우는 배치 — 명세 docs/api/auth-kakao-v1.md §5-3 · §5-4 (#46)

이 배치는 방어가 아니라 청소다. 만료된 값은 배치가 늦어도 통과하지 못한다 — 세션 판정은
expires_at 을 보고(api/deps/auth.py), 1회용 코드 소비는 `DELETE … WHERE expires_at > now()`
한 문장이다 (§5-4 "배치는 청소지 방어가 아니다").

그래도 지우는 이유는 둘이다.
  · 세션은 증빙이 아니라서 남은 행은 유출 표면일 뿐이다 (AuthSession 주석 "폐기는 행 삭제다").
  · 동의문 1 이 "로그인을 유지하기 위한 값은 최대 12시간" 이라고 알린다. 그리고 가입하다
    그만둔 사람의 auth_handoff 에는 카카오 회원번호가 들어 있다 — 계정을 만들지 않은 사람의
    식별값이 기한 없이 남는다.

스케줄러는 아직 붙이지 않았다. consent_purge 와 같이, 언제 어떻게 부를지(주기 · 운영 진입점)는
배포 구성이 정해진 뒤에 결정한다.

🚨 로그에는 지운 **건수**만 남긴다. 토큰 해시 · 회원번호 · 코드는 남기지 않는다 (§7-5).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import AuthHandoff, AuthSession

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PurgedAuthRows:
    sessions: int
    handoffs: int


async def purge_expired_auth_rows(session: AsyncSession, *, now: datetime) -> PurgedAuthRows:
    """expires_at 이 now 이하인 session · auth_handoff 행을 지우고 건수를 돌려준다.

    경계(`<=`)는 세션 판정과 같다 — 만료 시각과 정확히 같은 순간의 세션은 이미 401 이다.
    현재 시각은 호출자가 넘긴다 (consent_purge 와 같은 이유 — 테스트가 실제 시각에 묶이지 않게).
    """
    sessions = await session.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    handoffs = await session.execute(delete(AuthHandoff).where(AuthHandoff.expires_at <= now))
    await session.flush()

    purged = PurgedAuthRows(sessions=sessions.rowcount, handoffs=handoffs.rowcount)
    log.info(
        "만료된 로그인 행 정리: session %d건, auth_handoff %d건", purged.sessions, purged.handoffs
    )
    return purged
