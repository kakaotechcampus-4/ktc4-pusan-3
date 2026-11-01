"""푸시 기기 등록 · 보호자 알림 스위치 (#594)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, select, text
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

둘 다 켜짐 — 알림은 일정마다 직접 고르고 권한도 그때 허용하니, 설정 탭은 한 번에 끄는 곳이다.
기본이 꺼짐이면 고르고 허용까지 했는데 울리지 않는다 (10-29 PM 결정, #605).
행에 박지 않아서 바꾸면 만진 적 없는 보호자 모두에게 바로 적용된다
(`NotificationSetting` docstring).
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


async def schedule_push_targets(
    session: AsyncSession, *, parent_ids: list[uuid.UUID], now: datetime
) -> dict[uuid.UUID, list[tuple[uuid.UUID, str]]]:
    """일정 알림을 받을 기기 — {보호자: [(기기 id, 토큰)]}. 받지 않을 보호자는 키가 없다.

    - 일정 알림 스위치가 꺼진 보호자는 뺀다. 바꾼 적 없으면 `DEFAULT_SETTING` 을 따른다.
    - 로그인이 만료된 기기는 뺀다. 만료된 로그인은 하루 한 번 정리 배치가 지우는데, 그 전까지
      남아 있는 기기로 보내지 않는다 (인증 판정과 같은 경계 `<=`).
    """
    if not parent_ids:
        return {}
    stmt = (
        _schedule_targets(now)
        .where(PushDevice.parent_id.in_(parent_ids))
        .order_by(PushDevice.parent_id, PushDevice.id)
    )
    targets: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for parent_id, device_id, token in await session.execute(stmt):
        targets.setdefault(parent_id, []).append((device_id, token))
    return targets


async def still_schedule_target(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    device_id: uuid.UUID,
    device_token: str,
    now: datetime,
) -> bool:
    """보내기 바로 직전에 다시 본다 — 그 기기가 아직 그 보호자의 것이고, 토큰이 같고, 로그인이
    살아 있고, 일정 알림이 켜져 있나.

    `schedule_push_targets` 로 읽은 목록을 끝까지 쓰면, 배치가 보내는 사이 로그아웃하고 다른
    계정으로 로그인한 공용 기기에 앞 보호자의 알림(아이 · 일정 id)이 간다. 이 확인과 발송 사이의
    짧은 틈은 남는다 — 보내는 동안 행을 잡으면 로그아웃이 FCM 응답을 기다리게 돼서 잡지 않는다.
    """
    stmt = _schedule_targets(now).where(
        PushDevice.id == device_id,
        PushDevice.parent_id == parent_id,
        PushDevice.device_token == device_token,
    )
    return (await session.execute(stmt)).first() is not None


def _schedule_targets(now: datetime) -> Select:
    """일정 알림을 받을 수 있는 기기 — 로그인이 살아 있고 그 보호자의 일정 알림이 켜져 있다."""
    enabled = func.coalesce(NotificationSetting.schedule_enabled, DEFAULT_SETTING.schedule_enabled)
    return (
        select(PushDevice.parent_id, PushDevice.id, PushDevice.device_token)
        .join(AuthSession, AuthSession.id == PushDevice.session_id)
        .outerjoin(NotificationSetting, NotificationSetting.parent_id == PushDevice.parent_id)
        .where(
            AuthSession.refresh_expires_at > now,
            AuthSession.absolute_expires_at > now,
            enabled.is_(True),
        )
    )


async def forget_devices(session: AsyncSession, *, devices: list[tuple[uuid.UUID, str]]) -> None:
    """FCM 이 더는 유효하지 않다고 한 기기를 지운다 — [(기기 id, 그때 보낸 토큰)].

    토큰까지 맞춰 지운다. 보내는 사이에 같은 로그인이 새 토큰으로 다시 등록했으면 그 행은 남긴다.
    """
    for device_id, token in devices:
        await session.execute(
            delete(PushDevice).where(PushDevice.id == device_id, PushDevice.device_token == token)
        )
