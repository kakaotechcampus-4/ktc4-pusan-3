"""푸시 기기 등록 · 보호자 알림 스위치 (#594)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notification.models import NotificationSetting, PushDevice, PushPlatform


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
) -> None:
    """이 로그인의 기기를 이 토큰으로 둔다. 앱 실행 · 로그인 · 토큰 갱신마다 불러도 결과가 같다.

    - 같은 로그인에서 토큰이 바뀌면 그 행을 새 토큰으로 덮는다 (로그인 하나에 기기 하나).
    - 같은 토큰이 다른 로그인에 있으면 지운다. 같은 기기에서 다른 계정으로 로그인한 것이라, 앞
      계정의 알림이 이 기기로 가면 안 된다. 앞 로그인이 로그아웃했다면 이미 CASCADE 로 지워졌다.
    """
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


async def put_switches(session: AsyncSession, *, parent_id: uuid.UUID, switches: Switches) -> None:
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
