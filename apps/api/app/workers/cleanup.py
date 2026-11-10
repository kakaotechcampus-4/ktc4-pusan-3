"""만료된 값을 하루 한 번 지우는 배치의 진입점 — 서버 cron 이 부른다 (#166)

처리방침 v5 와 docs/safety/retention-v1.md 가 "로그인 유지 값은 하루 한 번 서버에서 지운다" 고
알린다. 지우는 일은 아래 함수들이 이미 한다. 여기서는 연결을 열고, 부르고, 커밋한다.

  · auth_cleanup.purge_expired_auth_rows — 만료된 로그인 세션 · 1회용 코드 · 가입 대기표
  · consent_purge.purge_expired_retentions — 보관 기간(1년)이 끝난 동의 증빙
  · location_log.purge_expired_location_logs — 6개월이 지난 위치 이용 확인자료
  · deletion_log_purge.purge_old_deletion_logs — 35일이 지난 삭제 이력 (#758)
  · usage.repository.purge_expired_llm_usage — 90일이 지난 LLM 사용량 (#710)

🚨 함수들은 flush 만 한다. 커밋은 여기서 한다 — 빠지면 지운 건수만 로그에 찍히고 실제로는
   하나도 지워지지 않는다. 앞의 넷(처방침이 약속한 정리)은 한 트랜잭션이라 하나가 실패하면 넷 다
   지우지 않는다.
🚨 LLM 사용량은 따로 다른 트랜잭션이다(뒤에, 커밋한 다음). BM 용 정리라서 이것이 실패해도
   (예: 배포 때 migrate 전이라 표가 없다) 처방침이 약속한 정리를 되감으면 안 된다.
   실패는 ERROR 로 알린다.
🚨 백업을 복원한 뒤에도 이걸 한 번 바로 돌린다 — 백업 시각 뒤에 보관 기간이 끝나 지운 것이
   백업에서 되살아나 있다 (#758 복원 절차).
🚨 로그에는 건수만 남긴다 (auth_cleanup 과 같은 규칙). 실패는 ERROR 로 찍혀 Discord 로 간다 —
   조용히 멈추면 처리방침의 "하루 한 번" 이 아무도 모르게 거짓이 된다.

  서버 cron 한 줄과 확인 방법은 apps/api/README.md "만료된 값 정리".
  로컬 한 번: cd apps/api && uv run python -m app.workers.cleanup
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.logging_config import configure_logging, log_error_without_message
from app.domains.consent.location_log import purge_expired_location_logs
from app.domains.usage.repository import purge_expired_llm_usage
from app.infra.db.session import async_session_factory, engine
from app.integrations import discord
from app.workers.auth_cleanup import PurgedAuthRows, purge_expired_auth_rows
from app.workers.consent_purge import purge_expired_retentions
from app.workers.deletion_log_purge import purge_old_deletion_logs

# __name__ 이 아니다 — `python -m` 으로 돌면 __name__ 이 "__main__" 이 되어 app.* 밖으로 빠진다.
# 그러면 INFO 건수가 버려지고, 실패 ERROR 가 Discord 로 가지 않는다 (알림은 app 로거에 붙는다)
log = logging.getLogger("app.workers.cleanup")


@dataclass(frozen=True)
class CleanupResult:
    auth: PurgedAuthRows
    retentions: int
    location_logs: int
    deletion_logs: int
    llm_usage: int


async def run_cleanup(session: AsyncSession, *, now: datetime) -> CleanupResult:
    """넷을 한 트랜잭션으로 돌리고 커밋한 다음, LLM 사용량 정리를 따로 한다.

    현재 시각은 호출자가 넘긴다 — 정리 함수들과 같은 이유(테스트가 실제 시각에 묶이지 않게).
    """
    auth = await purge_expired_auth_rows(session, now=now)
    retentions = await purge_expired_retentions(session, now=now)
    location_logs = await purge_expired_location_logs(session, now=now)
    deletion_logs = await purge_old_deletion_logs(session, now=now)
    await session.commit()
    log.info("만료된 동의 증빙 정리: %d건", retentions)
    log.info("만료된 위치 이용 기록 정리: %d건", location_logs)
    log.info("35일 지난 삭제 이력 정리: %d건", deletion_logs)
    llm_usage = await _purge_llm_usage(session, now=now)
    return CleanupResult(
        auth=auth,
        retentions=retentions,
        location_logs=location_logs,
        deletion_logs=deletion_logs,
        llm_usage=llm_usage,
    )


async def _purge_llm_usage(session: AsyncSession, *, now: datetime) -> int:
    """90일 지난 LLM 사용량을 지우고 건수를 돌려준다.

    실패하면 알리고 0 이다 — 예외를 내지 않는다.
    """
    try:
        deleted = await purge_expired_llm_usage(session, now=now)
        await session.commit()
    except Exception as exc:
        # 연결이 끊겼으면 rollback 도 예외를 낸다. 앞의 정리는 이미 커밋됐다
        with contextlib.suppress(Exception):
            await session.rollback()
        log_error_without_message(log, "LLM 사용량 정리 실패", exc)
        return 0
    log.info("90일 지난 LLM 사용량 정리: %d건", deleted)
    return deleted


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
    except Exception as exc:
        log_error_without_message(log, "만료된 값 정리 실패", exc)
        raise SystemExit(1) from None
