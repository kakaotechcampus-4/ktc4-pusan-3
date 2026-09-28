"""정책 조회 — 동의 화면이 그릴 약관 버전과 본문을 서버가 내려준다 (이슈 #91).

🚨 무인증이다 (루트 CLAUDE.md §9). 동의 화면은 계정이 만들어지기 전에 뜨므로 Bearer 토큰이
   없다. 약관은 누구나 읽을 수 있어야 하는 공개 문서라 막을 이유도 없다.
"""

from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps.db import SessionDep
from app.api.v1.schemas.policies import PolicyResponse
from app.domains.policy.catalog import SCOPE_CATALOG
from app.domains.policy.repository import find_active_versions

router = APIRouter(tags=["policies"])


@router.get("/policies")
async def list_policies(session: SessionDep) -> list[PolicyResponse]:
    """scope 마다 지금 유효한 정책 하나씩, 화면 순서대로 (#91).

    등록된 행이 없는 scope 와 동의 화면에서 묻지 않는 scope 는 담지 않는다.
    """
    active = {row.scope: row for row in await find_active_versions(session, now=datetime.now(UTC))}
    return [
        PolicyResponse(
            scope=scope,
            version=row.version,
            label=info.label,
            legal_basis=info.legal_basis,
            required=info.required,
            sensitive=info.sensitive,
            content=row.content,
        )
        for scope, info in SCOPE_CATALOG.items()
        if (row := active.get(scope)) is not None
    ]
