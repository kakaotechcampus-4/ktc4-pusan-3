"""만료된 run_hold 행 · 지난 rate_event 를 1분마다 지운다 (#877 · ops/stateless-v1.md "지우기").

처리방침이 되묻기 맥락을 "15분 안에 지운다" 고 적어서 하루 1회 정리 배치
(`workers/cleanup.py`)로는 모자란다. 읽을 때도 만료를 거르지만(`run_hold.repository`),
원문이 표에 남아 있지 않게 하는 것이 이 루프의 몫이다.

api lifespan 이 프로세스마다 하나 띄운다. 워커가 여럿이면 같이 돌지만 같은 행을 지울 뿐이라
무해하다. 오류가 나도 루프는 멈추지 않는다 — 다음 주기에 다시 지운다. 로그에는 예외 종류만
남긴다.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.db import background_session
from app.domains.rate_event.repository import delete_events_before
from app.domains.run_hold.repository import delete_expired_holds

log = logging.getLogger(__name__)

PURGE_INTERVAL_S = 60.0
RATE_KEEP = timedelta(days=2)
"""한도 카운터를 남기는 기간. 가장 긴 통이 하루(날짜별 입력)라 이틀이면 넉넉하다."""

_session_factory: Callable[[], AsyncSession] = background_session


async def purge_once(*, now: datetime | None = None) -> int:
    """지운 보관함 행 수를 돌려준다. 카운터는 같은 트랜잭션에서 함께 지운다."""
    async with _session_factory() as session:
        removed = await delete_expired_holds(session, now=now)
        await delete_events_before(session, before=(now or datetime.now(UTC)) - RATE_KEEP)
        await session.commit()
    return removed


async def run_purger(interval_s: float = PURGE_INTERVAL_S) -> None:
    while True:
        try:
            await purge_once()
        except Exception as exc:
            log.warning("만료된 run_hold 를 지우지 못했다 — %s", type(exc).__name__)
        await asyncio.sleep(interval_s)
