"""푸시 기기 등록 · 알림 켜고 끄기 — #594.

기기는 지금 로그인(Bearer 의 세션)에 묶인다. 로그아웃 · 세션 만료 · 탈퇴로 세션이 지워지면 기기도
함께 지워지므로 화면이 로그아웃할 때 따로 지울 필요가 없다 (`PushDevice` docstring).

둘 다 최종 상태를 보내는 PUT 이라 Idempotency-Key 를 받지 않는다 — 여러 번 보내도 결과가 같다.
🚨 토큰은 경로 · 쿼리가 아니라 본문으로 받는다. 접근 로그에 남지 않게 하려는 것이다.
"""

from fastapi import APIRouter, Response

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.notifications import NotificationSettings, PushDeviceIn
from app.domains.notification.repository import (
    Switches,
    get_switches,
    put_switches,
    register_device,
    unregister_device,
)

router = APIRouter()


@router.put(
    "/me/push-device",
    status_code=204,
    response_class=Response,
    responses={status: {"model": ErrorEnvelope} for status in (400, 401)},
)
async def put_my_push_device(
    auth: CurrentParent, session: SessionDep, body: PushDeviceIn
) -> Response:
    """이 로그인에서 알림을 받을 기기를 둔다. 앱 실행 · 로그인 · 토큰이 바뀔 때마다 부른다.

    같은 토큰이 다른 계정의 로그인에 있으면 그쪽에서 뺀다 — 같은 기기에서 계정을 바꾼 것이다.
    """
    registered = await register_device(
        session,
        parent_id=auth.parent_id,
        session_id=auth.session_id,
        device_token=body.token,
        platform=body.platform,
    )
    if not registered:  # 확인한 뒤 로그아웃됐다 — 인증 실패와 같게 본다
        raise ApiError(401, "unauthenticated", "다시 로그인해 주세요")
    await session.commit()
    return Response(status_code=204)


@router.delete("/me/push-device", status_code=204, response_class=Response)
async def delete_my_push_device(auth: CurrentParent, session: SessionDep) -> Response:
    """이 로그인에서 알림을 그만 받는다 (예: 기기에서 알림 권한을 껐을 때). 없어도 204."""
    await unregister_device(session, session_id=auth.session_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/me/notification-settings")
async def read_my_notification_settings(
    auth: CurrentParent, session: SessionDep
) -> NotificationSettings:
    """한 번도 바꾸지 않았으면 기본값이다."""
    switches = await get_switches(session, parent_id=auth.parent_id)
    return NotificationSettings(**vars(switches))


@router.put(
    "/me/notification-settings",
    responses={status: {"model": ErrorEnvelope} for status in (400, 401)},
)
async def put_my_notification_settings(
    auth: CurrentParent, session: SessionDep, body: NotificationSettings
) -> NotificationSettings:
    """두 스위치를 함께 보낸다. 내 모든 기기에 적용된다."""
    if not await put_switches(
        session, parent_id=auth.parent_id, switches=Switches(**body.model_dump())
    ):  # 확인한 뒤 탈퇴했다 — 인증 실패와 같게 본다
        raise ApiError(401, "unauthenticated", "다시 로그인해 주세요")
    await session.commit()
    return body
