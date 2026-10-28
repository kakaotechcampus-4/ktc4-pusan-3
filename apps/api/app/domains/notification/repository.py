"""푸시 기기 등록 · 보호자 알림 스위치 (#594)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import AuthSession, Parent
from app.domains.notification.models import NotificationSetting, PushDevice, PushPlatform

_DEVICE_TOKEN_LOCK_NS = 2
"""pg_advisory_xact_lock(int4, int4) 의 첫 번째 인자. Curator 는 1 (`curator/trigger.py`)."""


@dataclass(frozen=True)
class Switches:
    schedule_enabled: bool
    medication_enabled: bool


DEFAULT_SETTING = Switches(schedule_enabled=True, medication_enabled=True)
"""스위치를 한 번도 만지지 않은 보호자의 값.

🚨 잠정값이다 — 기본값은 PM 결정 대기 중이다 (#594). 정해지면 이 줄만 고친다. 행에 박지 않아서
   만진 적 없는 보호자 모두에게 바로 적용된다 (`NotificationSetting` docstring).
"""


async def register_device(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    session_id: uuid.UUID,
    device_token: str,
    platform: PushPlatform,
) -> bool:
    """이 로그인의 기기를 이 토큰으로 둔다. 앱 실행 · 로그인 · 토큰 갱신마다 불러도 결과가 같다.

    - 같은 로그인에서 토큰이 바뀌면 그 행을 새 토큰으로 덮는다 (로그인 하나에 기기 하나).
    - 같은 토큰이 다른 로그인에 있으면 지운다. 같은 기기에서 다른 계정으로 로그인한 것이라, 앞
      계정의 알림이 이 기기로 가면 안 된다. 앞 로그인이 로그아웃했다면 이미 CASCADE 로 지워졌다.

    로그인이 그사이 지워졌으면 쓰지 않고 False.
    🚨 보호자 → 로그인 행을 FOR KEY SHARE 로 잡은 뒤 쓴다. 인증 확인은 잠그지 않아서, 그 뒤에
       로그아웃이 커밋하면 없는 로그인을 가리키는 행을 넣다 FK 오류가 난다. 순서는 탈퇴(보호자를
       잡고 로그인을 지운다)와 같다 — 반대로 잡으면 서로 기다린다.
    🚨 같은 토큰은 한 번에 하나만 등록한다(advisory lock). 두 계정이 동시에 "다른 로그인의 행
       지우기"를 지나면 둘 다 넣으려다 토큰 유니크에 걸린다. 나중 것이 기다렸다가 앞 것을 지운다.
    """
    if not await _lock_parent(session, parent_id=parent_id):
        return False
    alive = await session.scalar(
        select(AuthSession.id)
        .where(AuthSession.id == session_id)
        .with_for_update(read=True, key_share=True)
    )
    if alive is None:
        return False
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:token))"),
        {"ns": _DEVICE_TOKEN_LOCK_NS, "token": device_token},
    )
    await session.execute(
        delete(PushDevice).where(
            PushDevice.device_token == device_token, PushDevice.session_id != session_id
        )
    )
    stmt = (
        pg_insert(PushDevice)
        .values(
            parent_id=parent_id,
            session_id=session_id,
            device_token=device_token,
            platform=platform,
        )
        .on_conflict_do_update(
            index_elements=[PushDevice.session_id],
            set_={
                "device_token": device_token,
                "platform": platform,
                "last_seen_at": func.now(),
                "updated_at": func.now(),  # ORM 의 onupdate 는 이 문장에 걸리지 않는다
            },
        )
    )
    await session.execute(stmt)
    return True


async def unregister_device(session: AsyncSession, *, session_id: uuid.UUID) -> None:
    """이 로그인의 기기를 지운다. 없으면 아무것도 하지 않는다."""
    await session.execute(delete(PushDevice).where(PushDevice.session_id == session_id))


async def get_switches(session: AsyncSession, *, parent_id: uuid.UUID) -> Switches:
    row = await session.scalar(
        select(NotificationSetting).where(NotificationSetting.parent_id == parent_id)
    )
    if row is None:
        return DEFAULT_SETTING
    return Switches(row.schedule_enabled, row.medication_enabled)


async def put_switches(session: AsyncSession, *, parent_id: uuid.UUID, switches: Switches) -> bool:
    """보호자가 그사이 탈퇴했으면 쓰지 않고 False.

    🚨 보호자 행을 FOR KEY SHARE 로 잡은 뒤 쓴다. 인증 확인은 잠그지 않아서, 그 뒤에 탈퇴가
       커밋하면 없는 보호자를 가리키는 행을 넣다 FK 오류(500)가 난다.
    """
    if not await _lock_parent(session, parent_id=parent_id):
        return False
    values = {
        "schedule_enabled": switches.schedule_enabled,
        "medication_enabled": switches.medication_enabled,
    }
    stmt = (
        pg_insert(NotificationSetting)
        .values(parent_id=parent_id, **values)
        .on_conflict_do_update(
            index_elements=[NotificationSetting.parent_id],
            set_={**values, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
    return True


async def _lock_parent(session: AsyncSession, *, parent_id: uuid.UUID) -> bool:
    """보호자 행을 FOR KEY SHARE 로 잡고 아직 있는지 돌려준다.

    탈퇴(`withdraw_parent`)가 먼저 잡는 행이라 다른 행보다 먼저 잡는다.
    """
    locked = await session.scalar(
        select(Parent.id).where(Parent.id == parent_id).with_for_update(read=True, key_share=True)
    )
    return locked is not None
