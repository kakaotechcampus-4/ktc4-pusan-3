"""화면 오류 보고 — `POST /client-errors` (멘토 #267 2번, #166).

보호자의 폰 · 브라우저에서 난 화면 오류는 그 기기의 콘솔에만 남고 서버로 오지 않았다. 화면
(apps/web/src/lib/report-render-error.ts)이 예외 종류 · digest · 화면 경로 · 기기 요약만 여기로
보내면, 서버가 ERROR 로 찍어 Discord 알림(app/core/alerts.py)이 그대로 간다 — 알림엔 글귀만, 값은
서버 로그에서 본다.

DB 에는 넣지 않는다 — 처리방침 ⑥ "기기 정보를 데이터베이스에 저장하지 않는다". 로그의 보관 기간이
곧 이 기록의 보관 기간이다 (docs/ops/logging-alerts-v1.md).

🚨 로그인한 보호자만 (protected_router). 인증 없는 창구면 아무나 팀 채널을 울려 진짜 알림을 묻는다.
   그래서 로그인 전 화면(로그인 · 동의)의 오류는 못 받는다 — 두 화면뿐이라 감수한다.
"""

import logging

from fastapi import APIRouter, status

from app.api.deps.auth import CurrentParent
from app.api.v1.schemas.client_errors import ClientErrorReport

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/client-errors", status_code=status.HTTP_204_NO_CONTENT)
async def report_client_error(body: ClientErrorReport, parent: CurrentParent) -> None:
    """화면 오류 한 건을 서버 로그(ERROR)로 남긴다. 응답은 없다 — 화면은 결과를 기다리지 않는다.

    alert_detail — 네 칸은 서버가 모양을 검사했으니(스키마 패턴) Discord 에도 띄운다. 보호자 id 는
    서버 로그에만. alert_source — Discord 에서 browser-alert 로 뜬다.
    """
    log.error(
        "화면 오류 name=%s digest=%s path=%s platform=%s parent=%s",
        body.name,
        body.digest,
        body.path,
        body.platform,
        parent.parent_id,
        extra={
            "alert_detail": f"{body.name} {body.digest or '-'} {body.path} {body.platform}",
            "alert_source": "browser",
        },
    )
