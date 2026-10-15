"""POST /auth/refresh — refresh 쿠키로 access 를 다시 받는다 (#466)

여기서 지키는 것 넷.
    ① 현재 refresh 면 새 access + 회전된 쿠키. 회전 전 access 도 만료 전까지 통한다 (탭 여러 개)
    ② 직전 refresh 는 회전 후 30초 안이면 access 만, 밖이면 세션째 지운다
    ③ 실패는 사유를 구분하지 않고 전부 401 + 쿠키 삭제
    ④ 같은 refresh 로 두 요청이 동시에 와도 한 번만 회전한다
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps.auth import hash_token
from app.api.v1.routers import auth as auth_router
from app.domains.identity.models import AuthSession, Parent
from app.domains.identity.repository import create_access
from app.infra.db.session import engine
from tests.integration.api.conftest import cookie_header, seed_login

URL = "/api/v1/auth/refresh"
ME = "/api/v1/me"


def with_refresh(value: str) -> dict[str, str]:
    """쿠키를 헤더로 직접 싣는다 — Secure 쿠키라 httpx 저장소가 http 테스트 주소로 싣지 않는다."""
    return {"Cookie": f"icatch_rt={value}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def cookie_value(response) -> str:
    return cookie_header(response, "icatch_rt").split(";")[0].split("=", 1)[1]


async def seed_refresh(
    session,
    *,
    refresh: str = "rt-current",
    prev: str | None = None,
    rotated_ago: timedelta | None = None,
    idle_left: timedelta = timedelta(days=30),
    absolute_left: timedelta = timedelta(days=90),
) -> uuid.UUID:
    """refresh 원문을 아는 세션 1건. 세션 id 를 돌려준다."""
    now = datetime.now(UTC)
    parent = Parent()
    session.add(parent)
    await session.flush()
    login = AuthSession(
        parent_id=parent.id,
        refresh_hash=hash_token(refresh),
        prev_refresh_hash=hash_token(prev) if prev else None,
        rotated_at=now - rotated_ago if rotated_ago is not None else None,
        refresh_expires_at=now + idle_left,
        absolute_expires_at=now + absolute_left,
    )
    session.add(login)
    await session.flush()
    return login.id


# ── ① 회전 ────────────────────────────────────────────────────────────────────


async def test_current_refresh_rotates_and_gives_a_working_access(db_client, session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    await seed_login(session, parent_id=parent.id, token="old-access")
    refresh = "refresh:old-access"  # seed_login 이 심는 refresh 원문

    response = await db_client.post(URL, headers=with_refresh(refresh))

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 900
    rotated = cookie_value(response)
    assert rotated != refresh
    assert "Max-Age=2592000" in cookie_header(response, "icatch_rt")
    # 새 access 가 통하고, 회전 전에 받은 access 도 만료 전까지 통한다 (다른 탭)
    assert (await db_client.get(ME, headers=bearer(body["token"]))).status_code == 200
    assert (await db_client.get(ME, headers=bearer("old-access"))).status_code == 200
    # 회전된 쿠키로 다시 refresh 할 수 있다
    assert (await db_client.post(URL, headers=with_refresh(rotated))).status_code == 200


async def test_access_never_outlives_the_absolute_limit(db_client, session):
    """로그인 상한이 15분보다 적게 남았으면 access 도 그만큼만 산다."""
    await seed_refresh(session, absolute_left=timedelta(minutes=5), idle_left=timedelta(minutes=5))

    response = await db_client.post(URL, headers=with_refresh("rt-current"))

    assert response.status_code == 200
    assert 0 < response.json()["expires_in"] <= 300


# ── ② 직전 refresh ────────────────────────────────────────────────────────────


async def test_previous_refresh_within_grace_gets_access_only(db_client, session):
    """동시 요청에서 진 쪽이다. 쿠키는 이긴 응답이 이미 바꿨으니 건드리지 않는다."""
    await seed_refresh(session, prev="rt-prev", rotated_ago=timedelta(seconds=5))

    response = await db_client.post(URL, headers=with_refresh("rt-prev"))

    assert response.status_code == 200
    assert cookie_header(response, "icatch_rt") is None
    assert (await db_client.get(ME, headers=bearer(response.json()["token"]))).status_code == 200


async def test_previous_refresh_after_grace_ends_the_login(db_client, session, caplog):
    """🚨 유예 밖의 직전 refresh 는 재사용으로 본다 — 세션째 지워 그 로그인의 access 도 바로 401.

    로그에는 세션 id 만 남는다. 토큰도 해시도 남기지 않는다 (§7-5).
    """
    session_id = await seed_refresh(session, prev="rt-prev", rotated_ago=timedelta(minutes=10))
    await create_access(
        session,
        session_id=session_id,
        token_hash=hash_token("live-access"),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    with caplog.at_level(logging.WARNING, logger=auth_router.log.name):
        response = await db_client.post(URL, headers=with_refresh("rt-prev"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert "Max-Age=0" in cookie_header(response, "icatch_rt")
    assert await session.get(AuthSession, session_id) is None
    assert (await db_client.get(ME, headers=bearer("live-access"))).status_code == 401
    assert str(session_id) in caplog.text
    assert "rt-prev" not in caplog.text
    assert hash_token("rt-prev").hex() not in caplog.text


# ── ③ 실패 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("headers", [{}, with_refresh("no-such-refresh")])
async def test_missing_or_unknown_refresh_is_401(db_client, headers):
    response = await db_client.post(URL, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert "Max-Age=0" in cookie_header(response, "icatch_rt")


@pytest.mark.parametrize(
    ("idle_left", "absolute_left"),
    [
        (timedelta(seconds=-1), timedelta(days=60)),  # 30일 동안 안 썼다
        (timedelta(seconds=-1), timedelta(seconds=-1)),  # 로그인부터 90일이 지났다
    ],
    ids=["idle", "absolute"],
)
async def test_expired_refresh_is_401_and_removes_the_login(
    db_client, session, idle_left, absolute_left
):
    session_id = await seed_refresh(session, idle_left=idle_left, absolute_left=absolute_left)

    response = await db_client.post(URL, headers=with_refresh("rt-current"))

    assert response.status_code == 401
    assert await session.get(AuthSession, session_id) is None


async def test_refresh_after_logout_is_401(db_client, session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    await seed_login(session, parent_id=parent.id, token="to-logout")
    await db_client.post("/api/v1/auth/logout", headers=bearer("to-logout"))

    response = await db_client.post(URL, headers=with_refresh("refresh:to-logout"))

    assert response.status_code == 401


# ── ④ 동시 요청 ───────────────────────────────────────────────────────────────


async def test_concurrent_refresh_rotates_once(client, monkeypatch):
    """🚨 같은 refresh 로 두 요청이 겹쳐도 회전은 한 번이다 (FOR UPDATE).

    회전한 쪽이 커밋 전에 잠시 멈추게 해 두 요청을 확실히 겹친다. 잠금이 없으면 나중 요청도
    아직 바뀌지 않은 행을 읽고 한 번 더 회전해, 두 응답이 서로 다른 쿠키를 심는다.
    핸들러가 직접 커밋해야 해서 실제 커넥션으로 돌리고 끝나면 지운다.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        parent = Parent()
        s.add(parent)
        await s.flush()
        await seed_login(s, parent_id=parent.id, token="race-access")
        await s.commit()

    original = auth_router.rotate_refresh

    async def slow_rotate(*args, **kwargs):
        await original(*args, **kwargs)
        await asyncio.sleep(0.3)

    monkeypatch.setattr(auth_router, "rotate_refresh", slow_rotate)
    try:
        first, second = await asyncio.gather(
            client.post(URL, headers=with_refresh("refresh:race-access")),
            client.post(URL, headers=with_refresh("refresh:race-access")),
        )
        assert (first.status_code, second.status_code) == (200, 200)
        rotated = [r for r in (first, second) if cookie_header(r, "icatch_rt") is not None]
        assert len(rotated) == 1
        async with factory() as s:
            stored = await s.scalar(
                select(AuthSession.refresh_hash).where(AuthSession.parent_id == parent.id)
            )
        assert stored == hash_token(cookie_value(rotated[0]))
    finally:
        async with factory() as s:
            await s.execute(delete(Parent).where(Parent.id == parent.id))
            await s.commit()
