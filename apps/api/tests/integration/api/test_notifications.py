"""푸시 기기 등록 · 알림 켜고 끄기 — #594."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.identity.models import Parent
from app.domains.identity.repository import delete_session
from app.domains.notification.models import PushDevice
from app.domains.notification.repository import DEFAULT_SETTING
from app.infra.db.session import engine
from tests.integration.api.conftest import Bearer, issue_bearer, seed_login

DEVICE = "/api/v1/me/push-device"
SETTINGS = "/api/v1/me/notification-settings"


async def _devices(session: AsyncSession) -> list[tuple]:
    rows = await session.execute(
        select(PushDevice.parent_id, PushDevice.device_token)
        .order_by(PushDevice.device_token)
        .execution_options(populate_existing=True)
    )
    return [tuple(r) for r in rows]


async def test_device_lives_as_long_as_the_login(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    """토큰이 바뀌면 같은 행을 덮고, 로그아웃하면 따로 지우지 않아도 사라진다."""
    headers, parent_id = bearer
    await db_client.put(DEVICE, json={"token": "fcm-a", "platform": "web"}, headers=headers)
    resp = await db_client.put(DEVICE, json={"token": "fcm-b", "platform": "web"}, headers=headers)
    assert resp.status_code == 204
    assert await _devices(session) == [(parent_id, "fcm-b")]

    assert (await db_client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert await _devices(session) == []


async def test_same_device_moves_to_the_account_that_logged_in_last(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    """같은 기기에서 계정을 바꾸면 앞 계정의 알림이 그 기기로 가지 않는다."""
    first_headers, _ = bearer
    second_headers, second = await issue_bearer(session, token="test-token-second")
    body = {"token": "fcm-shared", "platform": "android"}
    await db_client.put(DEVICE, json=body, headers=first_headers)
    await db_client.put(DEVICE, json=body, headers=second_headers)
    assert await _devices(session) == [(second, "fcm-shared")]

    assert (await db_client.delete(DEVICE, headers=second_headers)).status_code == 204
    assert await _devices(session) == []


async def test_switches_start_at_the_default_and_keep_what_was_set(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    other_headers, _ = await issue_bearer(session, token="test-token-other")
    resp = await db_client.get(SETTINGS, headers=headers)
    assert resp.json() == vars(DEFAULT_SETTING)

    off = {"schedule_enabled": False, "medication_enabled": True}
    assert (await db_client.put(SETTINGS, json=off, headers=headers)).json() == off
    assert (await db_client.get(SETTINGS, headers=headers)).json() == off
    assert (await db_client.get(SETTINGS, headers=other_headers)).json() == vars(DEFAULT_SETTING)


# 다른 요청과 겹칠 때. 핸들러도 자기 세션으로 커밋해야 겹치므로 `client` 와 커밋하는 세션을 쓰고,
# 끝나면 만든 보호자를 지운다 (로그인 · 기기는 CASCADE).


@pytest.fixture
async def committed_logins():
    """(세션 공장, [(헤더, 보호자 id, 로그인 id)] × 2) — 실제로 커밋된 값."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        parents = [Parent(), Parent()]
        setup.add_all(parents)
        await setup.flush()
        logins = []
        for i, parent in enumerate(parents):
            token = f"test-token-device-race-{i}"
            login = await seed_login(setup, parent_id=parent.id, token=token)
            logins.append(({"Authorization": f"Bearer {token}"}, parent.id, login))
        await setup.commit()
    yield factory, logins
    async with factory() as cleanup:
        await cleanup.execute(delete(Parent).where(Parent.id.in_([p.id for p in parents])))
        await cleanup.commit()


async def test_logout_in_flight_leaves_no_device(client: AsyncClient, committed_logins):
    """로그아웃이 커밋하기 전에 기기 등록이 들어오면, 등록은 401 이고 기기가 남지 않는다."""
    factory, [(headers, _, login), _] = committed_logins
    async with factory() as logout:
        await delete_session(logout, session_id=login)
        putting = asyncio.create_task(
            client.put(DEVICE, json={"token": "fcm-race", "platform": "web"}, headers=headers)
        )
        await asyncio.sleep(0.3)  # 등록이 로그인 행 잠금에서 기다리게 둔다
        await logout.commit()

    assert (await asyncio.wait_for(putting, timeout=5)).status_code == 401
    async with factory() as check:
        assert await _devices(check) == []


async def test_two_accounts_registering_one_device_at_once(client: AsyncClient, committed_logins):
    """같은 기기의 두 계정이 한꺼번에 등록해도 오류 없이 한 계정에만 남는다."""
    factory, logins = committed_logins
    body = {"token": "fcm-race", "platform": "android"}
    responses = await asyncio.gather(
        *(client.put(DEVICE, json=body, headers=headers) for headers, _, _ in logins)
    )
    assert [r.status_code for r in responses] == [204, 204]
    async with factory() as check:
        assert len(await _devices(check)) == 1
