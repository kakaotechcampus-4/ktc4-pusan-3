"""만료된 값을 하루 한 번 지우는 배치의 진입점 — 서버 cron 이 부른다 (#166)

처리방침 v5 와 docs/safety/retention-v1.md 가 "로그인 유지 값은 하루 한 번 서버에서 지운다" 고
알린다. 지우는 일은 두 함수가 이미 한다. 여기서는 연결을 열고, 둘을 부르고, 커밋한다.

  · auth_cleanup.purge_expired_auth_rows — 만료된 로그인 세션 · 1회용 코드 · 가입 대기표
  · consent_purge.purge_expired_retentions — 보관 기간(1년)이 끝난 동의 증빙

🚨 두 함수는 flush 만 한다. 커밋은 여기서 한다 — 빠지면 지운 건수만 로그에 찍히고 실제로는
   하나도 지워지지 않는다. 둘을 한 트랜잭션으로 묶어서, 하나가 실패하면 둘 다 지우지 않는다.
🚨 로그에는 건수만 남긴다 (auth_cleanup 과 같은 규칙). 실패는 ERROR 로 찍혀 Discord 로 간다 —
   조용히 멈추면 처리방침의 "하루 한 번" 이 아무도 모르게 거짓이 된다.

  서버 cron 한 줄과 확인 방법은 apps/api/README.md "만료된 값 정리".
  로컬 한 번: cd apps/api && uv run python -m app.workers.cleanup
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.infra.db.session import async_session_factory, engine
from app.integrations import discord
from app.workers.auth_cleanup import PurgedAuthRows, purge_expired_auth_rows
from app.workers.consent_purge import purge_expired_retentions

# __name__ 이 아니다 — `python -m` 으로 돌면 __name__ 이 "__main__" 이 되어 app.* 밖으로 빠진다.
# 그러면 INFO 건수가 버려지고, 실패 ERROR 가 Discord 로 가지 않는다 (알림은 app 로거에 붙는다)
log = logging.getLogger("app.workers.cleanup")


@dataclass(frozen=True)
class CleanupResult:
    auth: PurgedAuthRows
    retentions: int


async def run_cleanup(session: AsyncSession, *, now: datetime) -> CleanupResult:
    """두 정리를 한 트랜잭션으로 돌리고 커밋한다.

    현재 시각은 호출자가 넘긴다 — 두 함수와 같은 이유(테스트가 실제 시각에 묶이지 않게).
    """
    auth = await purge_expired_auth_rows(session, now=now)
    retentions = await purge_expired_retentions(session, now=now)
    await session.commit()
    log.info("만료된 동의 증빙 정리: %d건", retentions)
    return CleanupResult(auth=auth, retentions=retentions)


async def _main() -> None:
    try:
        async with async_session_factory() as session:
            await run_cleanup(session, now=datetime.now(UTC))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    # main.py 와 같은 순서 — configure_logging() 이 app 로거 처리기를 갈아 끼우므로 알림은 그 뒤
    configure_logging()
    if settings.alert_webhook_url:
        configure_alerts(discord.webhook_sender(settings.alert_webhook_url), env=settings.APP_ENV)
    try:
        asyncio.run(_main())
    except Exception:
        log.exception("만료된 값 정리 실패")
        raise SystemExit(1) from None
