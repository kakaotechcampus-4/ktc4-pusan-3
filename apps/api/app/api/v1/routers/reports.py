"""앱 안 신고 · 문의 — `POST /reports` (#487).

Play 정책 두 개가 "앱을 나가지 않고" 신고하는 길을 요구한다 — AI 생성 콘텐츠 정책(AI 답 · 추천)과
사용자 제작 콘텐츠 정책(공동 기록 · 다른 보호자). 메일 앱으로 넘기는 `mailto:` 는 앱 밖이라 이
창구를 둔다.

🚨 로그인한 보호자만 (protected_router). 인증 없는 창구면 아무나 팀 채널을 울린다.
🚨 글(`message`)은 DB 에만 둔다. 로그 · Discord 에는 분류와 신고 번호만 — 보호자가 쓴 글에 아이
   정보가 섞일 수 있다 (루트 CLAUDE.md §2). 운영자는 번호로 행을 찾아 읽는다.
🚨 보호자당 24시간에 REPORT_DAILY_LIMIT 건까지. 신고 알림과 진짜 장애 알림이 Discord 의 분당
   한도(app/core/alerts.py)를 같이 쓰므로, 한 사람이 연타해 장애 알림을 묻지 못하게 한다.
   DB 로 세서 서버를 다시 켜도 그대로다. 세기 전에 보호자 행을 잠가 동시에 와도 넘치지 않는다
   (count_reports_since).
🚨 ERROR 로 찍는 이유는 오류라서가 아니라 Discord 로 가는 길이 ERROR 하나라서다
   (app/core/alerts.py). 출처가 report 라 Discord 에서 report-alert · 🟡 로 뜨고, 바닥글이 조회
   SQL 이라 오류 알림과 섞이지 않는다. 분당 한도는 오류 알림과 같이 쓴다.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, status

from app.api.deps.auth import CurrentParent
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.v1.schemas.reports import ReportOut, ReportRequest
from app.domains.report.repository import count_reports_since, create_report

log = logging.getLogger(__name__)

router = APIRouter()

REPORT_DAILY_LIMIT = 10
"""보호자당 24시간 신고 상한. 근거 없이 정한 시작 숫자다 — 실제로 막히는 사람이 나오면 다시 본다."""


@router.post(
    "/reports",
    status_code=status.HTTP_201_CREATED,
    responses={status: {"model": ErrorEnvelope} for status in (400, 429)},
)
async def submit_report(
    body: ReportRequest, parent: CurrentParent, session: SessionDep
) -> ReportOut:
    """신고 1건을 저장하고 팀 채널에 알린다. 같은 내용을 두 번 보내면 두 건이 된다."""
    since = datetime.now(UTC) - timedelta(days=1)
    sent = await count_reports_since(session, reporter_parent_id=parent.parent_id, since=since)
    if sent >= REPORT_DAILY_LIMIT:
        raise ApiError(
            429, "too_many_reports", "오늘은 더 보낼 수 없어요. 급한 일이면 이메일로 알려 주세요."
        )
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
            "alert_source": "report",
            "alert_heading": "신고 접수",
            "alert_detail": f"{row.category.value} · `{row.id}`",
        },
    )
    return out
