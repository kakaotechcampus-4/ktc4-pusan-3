"""운영용 헬스체크 — /api/v1 밖이라 인증 대상이 아니다 (루트 CLAUDE.md §9).

· /health        프로세스가 살아 있는가. docker healthcheck 가 본다 — DB 가 죽었다고 api 를
                 재시작해 봐야 낫지 않고 재시작만 반복하므로 의존성을 보지 않는다.
· /health/ready  DB · Redis 에 닿는가. 하나라도 안 되면 503.
                 서버 cron(deploy/scripts/health-alert.sh)이 본다 (#671).
                 응답에는 구성요소별 ok/fail 만 — 접속 주소 · 오류 글은 내지 않는다.
"""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps.db import background_session
from app.core.config import settings
from app.infra.redis import get_redis

router = APIRouter()
log = logging.getLogger(__name__)

READY_TIMEOUT_S = 2.0


@router.get("/health")
def health_check() -> dict:
    return {"status": "ok", "env": settings.APP_ENV}


async def _db() -> None:
    # 라우터는 infra 세션을 직접 쓰지 않는다 (apps/api/CLAUDE.md) — 요청 밖 세션 통로를 빌린다
    async with background_session() as session:
        await session.execute(text("SELECT 1"))


async def _redis() -> None:
    r = get_redis()
    if r is not None:  # REDIS_URL 이 없으면 메모리 폴백이 정상 구성이다
        await asyncio.to_thread(r.ping)


_stuck: set[asyncio.Task] = set()
"""시간 안에 못 끝낸 확인 — 취소만 걸고 기다리지 않는다. 끝날 때까지 여기서 붙잡아 GC 를 막는다."""


async def _check(name: str, probe) -> str:
    # 🚨 asyncio.timeout 으로 감싸면 안 된다. 취소는 한 번뿐이라, 멈춘 DB 연결을 닫는 정리
    #    (세션 rollback · close)가 다시 매달려 응답이 DB 가 돌아올 때까지 붙잡힌다
    #    (#671 리뷰에서 재현). 확인을 따로 띄우고, 시간이 지나면 정리를 안 기다리고 fail 로 답한다.
    task = asyncio.create_task(probe())
    done, _ = await asyncio.wait({task}, timeout=READY_TIMEOUT_S)
    if not done:
        task.cancel()
        _stuck.add(task)
        task.add_done_callback(_stuck.discard)
        log.warning("health/ready %s 실패: TimeoutError", name)
        return "fail"
    if (exc := task.exception()) is not None:
        log.warning("health/ready %s 실패: %s", name, type(exc).__name__)
        return "fail"
    return "ok"


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    checks = dict(
        zip(("db", "redis"), await asyncio.gather(_check("db", _db), _check("redis", _redis)))
    )
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "fail", "checks": checks}, status_code=200 if ok else 503
    )
