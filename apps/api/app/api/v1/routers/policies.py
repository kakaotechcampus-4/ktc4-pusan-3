"""정책 조회 — 동의 화면이 그릴 약관 버전과 본문을 서버가 내려준다 (이슈 #91 · #172).

🚨 무인증이다 (루트 CLAUDE.md §9). 동의 화면은 계정이 만들어지기 전에 뜨므로 Bearer 토큰이
   없다. 약관은 누구나 읽을 수 있어야 하는 공개 문서라 막을 이유도 없다.
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.deps.db import SessionDep
from app.api.errors import ApiError
from app.api.v1.schemas.common import ErrorEnvelope
from app.api.v1.schemas.policies import PolicyResponse
from app.domains.consent.models import ConsentScope
from app.domains.policy.catalog import SCOPE_CATALOG
from app.domains.policy.repository import find_active_versions, find_version

router = APIRouter(tags=["policies"])

PAGE_HEADERS = {
    # 정본은 스타일만 쓴다. 스크립트 · 외부 불러오기 · 폼 제출을 헤더로도 막는다 — 저장된 HTML 에
    # 무엇이 섞여 들어오더라도 이 페이지에서는 실행되지 않는다. frame-ancestors 는 두지 않는다:
    # 동의 화면이 iframe 으로 띄울 수 있어야 한다.
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    # 한 버전의 정본은 바뀌지 않는다 (policy_version 은 immutable). 하루는 캐시해도 된다.
    "Cache-Control": "public, max-age=86400",
}


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
            label=row.label,
            legal_basis=row.legal_basis,
            required=info.required,
            sensitive=info.sensitive,
            content=row.content,
            html_path=(
                f"/policies/{scope.value}/{row.version}" if row.content_html is not None else None
            ),
        )
        for scope, info in SCOPE_CATALOG.items()
        if (row := active.get(scope)) is not None
    ]


@router.get(
    "/policies/{scope}/{version}",
    response_class=HTMLResponse,
    responses={
        200: {"content": {"text/html": {}}, "description": "저장된 정본 HTML 그대로"},
        404: {"model": ErrorEnvelope},
    },
)
async def read_policy_page(scope: ConsentScope, version: str, session: SessionDep) -> HTMLResponse:
    """약관 한 버전의 정본 HTML — 동의 화면의 "전문 보기" 가 연다 (#172, 멘토 #71-4).

    서버가 저장해 둔 완성본을 그대로 내준다. 여기서 다시 만들지 않는다. 화면은 이 페이지를
    손대지 않고 띄우므로 "저장된 정본 = 보호자가 본 것" 이 된다.

    끝난 옛 버전도 내준다 — 그 글에 동의한 기록이 남아 있고, "변경 전 약관" 을 볼 수 있어야 한다.
    정본이 없는 자리 표시 글(draft-0)과 없는 버전은 404 다.
    """
    row = await find_version(session, scope=scope, version=version)
    if row is None or row.content_html is None:
        raise ApiError(404, "not_found", "약관을 찾을 수 없어요")
    return HTMLResponse(row.content_html, headers=PAGE_HEADERS)
