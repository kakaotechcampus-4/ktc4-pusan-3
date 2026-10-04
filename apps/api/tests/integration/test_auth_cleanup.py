"""만료된 로그인 세션 · 1회용 코드 정리 배치 (#46)

실제 Postgres 를 본다. 각 테스트는 바깥 트랜잭션을 마지막에 롤백한다 (tests/conftest.py).

🚨 NOW 를 과거의 고정 시각으로 둔다. 로컬에서 앱을 써 본 개발자의 DB 에는 커밋된 세션이
   남아 있고, 그 행들은 전부 NOW 보다 뒤에 만료된다 — 그래서 이 테스트의 삭제 건수에 섞이지
   않는다. "지금" 을 쓰면 그 행들까지 지워 세고, 빈 DB 에서만 통과한다 (#45).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domains.identity.models import AuthHandoff, AuthProvider, AuthSession, Parent
from app.workers.auth_cleanup import purge_expired_auth_rows

NOW = datetime(2026, 1, 1, tzinfo=UTC)
KAKAO_USER_ID = "cleanup-test-user"


async def seed_session(session, parent: Parent, *, expires_at: datetime, tag: bytes) -> AuthSession:
    row = AuthSession(parent_id=parent.id, token_hash=tag.ljust(32, b"-"), expires_at=expires_at)
    session.add(row)
    await session.flush()
    return row


async def seed_handoff(session, *, expires_at: datetime, tag: bytes) -> AuthHandoff:
    """가입하다 그만둔 사람이 남긴 대기표 — 카카오 회원번호가 들어 있다."""
    row = AuthHandoff(
        provider=AuthProvider.KAKAO,
        code_hash=tag.ljust(32, b"-"),
        bind_hash=b"b" * 32,
        provider_user_id=KAKAO_USER_ID,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row


async def remaining(session, model, rows) -> set:
    ids = {row.id for row in rows}
    return set(await session.scalars(select(model.id).where(model.id.in_(ids))))


async def test_only_expired_rows_are_deleted(session):
    """만료된 행만 지우고 살아 있는 행은 남긴다.

    만료 시각과 정확히 같은 순간은 만료다 — 세션 판정(`expires_at <= now` 면 401)과
    1회용 코드 소비(`expires_at > now` 만 유효)가 쓰는 경계와 같다.
    """
    parent = Parent()
    session.add(parent)
    await session.flush()
    expired = await seed_session(session, parent, expires_at=NOW - timedelta(hours=1), tag=b"s1")
    boundary = await seed_session(session, parent, expires_at=NOW, tag=b"s2")
    alive = await seed_session(session, parent, expires_at=NOW + timedelta(hours=1), tag=b"s3")
    old_ticket = await seed_handoff(session, expires_at=NOW - timedelta(minutes=5), tag=b"h1")
    live_ticket = await seed_handoff(session, expires_at=NOW + timedelta(minutes=5), tag=b"h2")

    purged = await purge_expired_auth_rows(session, now=NOW)

    assert (purged.sessions, purged.handoffs) == (2, 1)
    assert await remaining(session, AuthSession, [expired, boundary, alive]) == {alive.id}
    assert await remaining(session, AuthHandoff, [old_ticket, live_ticket]) == {live_ticket.id}


async def test_nothing_to_delete_is_zero(session):
    """지울 것이 없으면 0건이다. 에러가 아니다."""
    purged = await purge_expired_auth_rows(session, now=NOW)

    assert (purged.sessions, purged.handoffs) == (0, 0)


async def test_log_has_counts_but_no_identifiers(session, caplog):
    """🚨 로그에는 지운 건수만 남긴다 — 회원번호 · 토큰 · 코드는 남기지 않는다 (명세 §7-5)."""
    await seed_handoff(session, expires_at=NOW - timedelta(minutes=5), tag=b"h3")

    with caplog.at_level(logging.INFO, logger="app.workers.auth_cleanup"):
        await purge_expired_auth_rows(session, now=NOW)

    assert "auth_handoff 1건" in caplog.text
    assert KAKAO_USER_ID not in caplog.text
