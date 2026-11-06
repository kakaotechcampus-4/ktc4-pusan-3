"""화면 가이드(화면 투어)를 본 기록 — #696 · `docs/web/screen-tour-v1.md` §2.

계정 단위다 — 아이를 바꿔도, 기기를 바꿔도 같은 기록을 본다.
PUT · DELETE 는 여러 번 보내도 결과가 같아서 Idempotency-Key 를 받지 않는다 (되돌릴 수 있는 값이다).
"""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.tours import TOUR_KEYS, SeenTours, TourKey
from app.domains.tour.repository import clear_seen, list_seen, mark_seen

router = APIRouter()


@router.get("/me/tours")
async def read_my_tours(auth: CurrentParent, session: SessionDep) -> SeenTours:
    """본 가이드 목록. 한 번도 안 봤으면 빈 목록이다."""
    keys = await list_seen(session, parent_id=auth.parent_id)
    # 화면이 없어져 스키마에서 빠진 이름은 내보내지 않는다 — 남은 행 때문에 응답 검증이 깨지지 않게
    return SeenTours(seen=[k for k in keys if k in TOUR_KEYS])


@router.put(
    "/me/tours/{tour_key}",
    status_code=204,
    responses={status: {"model": ErrorEnvelope} for status in (401, 422)},
)
async def mark_my_tour_seen(
    auth: CurrentParent,
    session: SessionDep,
    tour_key: Annotated[TourKey, Path(description="본 가이드 이름 (`SeenTours.seen` 과 같은 값)")],
) -> Response:
    """본 것으로 남긴다. 끝까지 봤든 그만 봤든 같다.

    이미 본 가이드를 ? 로 다시 봤을 때도 부른다 — 처음 본 시각은 그대로 두고 204 다.
    """
    if not await mark_seen(session, parent_id=auth.parent_id, tour_key=tour_key):
        # 확인한 뒤 탈퇴했다 — 인증 실패와 같게 본다
        raise ApiError(401, "unauthenticated", "다시 로그인해 주세요")
    await session.commit()
    return Response(status_code=204)


@router.delete("/me/tours", status_code=204)
async def clear_my_tours(auth: CurrentParent, session: SessionDep) -> Response:
    """설정 › 고객센터 "화면 설명 다시 보기". 다음에 각 화면에 들어가면 가이드가 다시 뜬다."""
    await clear_seen(session, parent_id=auth.parent_id)
    await session.commit()
    return Response(status_code=204)
