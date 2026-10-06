"""내 정보 — `GET /me` (#92). 로그인 직후 화면이 어디로 보낼지 정하는 데 쓴다.

아이가 0명이면 화면은 /start(새로 등록 · 초대로 참여)로 간다.
"""

from fastapi import APIRouter

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.quota import today_kst
from app.api.v1.schemas.me import MeChild, MeResponse
from app.domains.child.repository import list_child_links_for_parent
from app.domains.consent.repository import missing_child_scopes
from app.domains.identity.repository import find_parent
from app.rules.age import age_display

router = APIRouter()


@router.get("/me")
async def read_me(parent: CurrentParent, session: SessionDep) -> MeResponse:
    """보호자와 연결된 아이들 — owner 는 등록한 보호자, 나머지는 초대로 연결된 member."""
    row = await find_parent(session, parent.parent_id)
    today = today_kst()
    children = [
        MeChild(
            child_id=child.id,
            nickname=child.nickname,
            age_display=age_display(child.birth_date, today),
            relation=relation,
            role="owner" if child.owner_parent_id == parent.parent_id else "member",
            consent_required=await missing_child_scopes(session, child_id=child.id),
        )
        for child, relation in await list_child_links_for_parent(
            session, parent_id=parent.parent_id
        )
    ]
    # 인증 의존성이 탈퇴 · 없는 계정을 이미 막았다 (deps/auth.py) — 여기서 row 는 있다
    return MeResponse(id=parent.parent_id, nickname=row.nickname, children=children)
