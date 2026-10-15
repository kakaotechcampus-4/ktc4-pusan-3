"""앱 안 신고 · 문의 — `POST /reports` (#487).

Play 정책 두 개가 "앱을 나가지 않고" 신고하는 길을 요구한다 — AI 생성 콘텐츠 정책(AI 답 · 추천)과
사용자 제작 콘텐츠 정책(공동 기록 · 다른 보호자). 메일 앱으로 넘기는 `mailto:` 는 앱 밖이라 이
창구를 둔다.

🚨 로그인한 보호자만 (protected_router). 인증 없는 창구면 아무나 팀 채널을 울린다.
🚨 글(`message`)은 DB 에만 둔다. 로그 · Discord 에는 분류와 신고 번호만 — 보호자가 쓴 글에 아이
   정보가 섞일 수 있다 (루트 CLAUDE.md §2). 운영자는 번호로 행을 찾아 읽는다.
🚨 ERROR 로 찍는 이유는 오류라서가 아니라 Discord 로 가는 길이 ERROR 하나라서다
   (app/core/alerts.py). 머리글이 "신고 접수" 라 오류 알림과 섞이지 않는다.
"""

import logging

from fastapi import APIRouter, status

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ErrorEnvelope
from app.api.v1.schemas.reports import ReportOut, ReportRequest
from app.domains.report.repository import create_report

log = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/reports",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorEnvelope}},
)
async def submit_report(
    body: ReportRequest, parent: CurrentParent, session: SessionDep
) -> ReportOut:
    """신고 1건을 저장하고 팀 채널에 알린다. 같은 내용을 두 번 보내면 두 건이 된다."""
    row = await create_report(
        session,
        reporter_parent_id=parent.parent_id,
        category=body.category,
        message=body.message,
    )
    out = ReportOut(id=row.id, created_at=row.created_at)
    await session.commit()
    log.error(
        "신고 접수 category=%s report=%s parent=%s",
        row.category.value,
        row.id,
        parent.parent_id,
        extra={
            "alert_heading": "신고 접수",
            "alert_detail": f"{row.category.value} · `{row.id}`",
        },
    )
    return out
