"""사용량 writer — `core/llm_usage` 의 큐를 2초마다 비워 `llm_usage` 표에 쓴다 (#710).

api lifespan 이 프로세스마다 하나 띄운다(`main.py`). 큐도 writer 도 프로세스마다 하나라
`--workers` 를 늘려도 맞다 — run 채널(#134)과 달리 프로세스 사이에 나눌 상태가 없다.

- 켜는 것도 이 writer 다(`llm_usage.enable()`).
  테스트 · eval · 스크립트는 writer 가 없어 큐도 경고도 없다.
- 2초마다 짧은 세션 1개를 열어 한 문장으로 쓴다 — 호출마다 세션을 열지 않아 풀 압박이 없다.
- 🚨 저장이 실패해도 run · 추천 · 삭제는 모른다. 이 task 는 예외를 밖으로 내지 않는다.
  - 탈퇴가 보호자 행을 지우는 순간과 겹치면 FK 위반(`IntegrityError`)이 난다. 새 세션으로 한 번 더
    쓰면 `LEFT JOIN` 이 보호자 칸을 비워 넣는다.
  - 연결 · 풀 오류(`_TRANSIENT`)는 잠깐일 수 있어 묶음을 큐 뒤에 되돌리고 그 주기를 끝낸다.
    다음 주기에 같은 묶음이 다시 나간다. 메모리는 큐 상한(`MAX_PENDING`)이 지킨다 — 그보다 오래
    못 쓰면 넘치는 만큼만 버린다.
  - 그 밖의 실패(스키마가 어긋난 버그 · 값 오류)는 묶음을 버린다. 되돌리면 같은 실패를 영원히 돈다.
    버린 뒤에는 그 주기를 끝낸다 — 한꺼번에 큐 전체를 버리지 않는다.
- 🚨 멈출 때(lifespan 종료 → 취소) 쓰던 묶음은 큐로 되돌려 마지막 flush 가 쓴다.
  마지막 flush 는 `FINAL_FLUSH_TIMEOUT_S` 안에서만 기다린다 — DB 가 안 닿는 채로 종료해도
  lifespan 이 SIGKILL 까지 붙들리지 않게. SIGKILL · OOM 이면 최대 2초분을 잃는다.
  종료 뒤에 끝나는 run · Curator 의 기록은 `disable()` 이후라 남지 않는다.
- 🚨 로그에는 건수 · 예외 종류만 있다 (루트 §2). 첫 실패만 ERROR(→ Discord)고 이어지는 실패는
  WARNING 이다 — DB 가 죽으면 2초마다 알림이 울린다. 고쳐지면 INFO 한 줄.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence

from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.db import background_session
from app.core import llm_usage
from app.core.logging_config import log_error_without_message
from app.domains.usage.repository import insert_usage

log = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 2.0
FINAL_FLUSH_TIMEOUT_S = 5.0
"""종료 때 마지막 flush 를 기다리는 시간. docker 의 정지 유예(10초) 안에 끝나야 한다."""

OpenSession = Callable[[], AsyncSession]

# 잠깐일 수 있는 오류 — 연결이 끊겼거나 풀이 말랐거나 DB 가 응답하지 않는다
_TRANSIENT = (OperationalError, InterfaceError, PoolTimeoutError, OSError, TimeoutError)

_failing = False
"""직전 저장이 실패했나 — ERROR 를 첫 실패에만 내려는 상태다."""


async def _insert(batch: Sequence[llm_usage.UsageRecord], open_session: OpenSession) -> int:
    committed = False
    try:
        async with open_session() as session:
            written = await insert_usage(session, batch)
            await session.commit()
            committed = True
            return written
    except asyncio.CancelledError:
        if not committed:
            llm_usage.requeue(batch)  # 멈추는 중이다 — 마지막 flush 가 쓴다
        raise


def _recovered() -> None:
    global _failing
    if _failing:
        _failing = False
        log.info("llm usage 저장이 다시 된다")


def _report_failure(exc: BaseException, *, what: str) -> None:
    """첫 실패만 ERROR(고정 글귀 — 건수는 WARNING 에 싣는다), 이어지면 WARNING."""
    global _failing
    if not _failing:
        _failing = True
        log_error_without_message(log, "llm usage 저장 실패", exc)
    else:
        log.warning("llm usage 저장이 계속 실패한다 — %s (%s)", what, type(exc).__name__)


def _kept(batch: Sequence[llm_usage.UsageRecord], exc: BaseException) -> None:
    queued = llm_usage.requeue(batch)
    _report_failure(exc, what=f"{queued}건을 다시 큐에 넣었다")


def _dropped(batch: Sequence[llm_usage.UsageRecord], exc: BaseException) -> None:
    _report_failure(exc, what=f"{len(batch)}건을 버렸다")


async def _write(batch: Sequence[llm_usage.UsageRecord], open_session: OpenSession) -> int | None:
    """묶음 하나를 쓴다. 쓴 행 수, 못 썼으면 None (되돌렸거나 버렸다)."""
    try:
        written = await _insert(batch, open_session)
    except IntegrityError:
        # 탈퇴와 겹쳤다 — 새 세션으로 한 번 더. 이번에는 LEFT JOIN 이 보호자 칸을 비운다
        try:
            written = await _insert(batch, open_session)
        except _TRANSIENT as exc:
            _kept(batch, exc)
            return None
        except Exception as exc:
            _dropped(batch, exc)
            return None
    except _TRANSIENT as exc:
        _kept(batch, exc)
        return None
    except Exception as exc:
        _dropped(batch, exc)
        return None
    _recovered()
    return written


async def flush(open_session: OpenSession = background_session) -> int:
    """큐가 빌 때까지 한 묶음(500건)씩 쓰고 쓴 행 수의 합을 돌려준다. 예외를 내지 않는다."""
    written = 0
    while batch := llm_usage.drain():
        saved = await _write(batch, open_session)
        if saved is None:
            break  # DB 가 아픈 중이다 — 남은 것(되돌린 묶음 포함)은 다음 주기에
        written += saved
    return written


async def run_writer(
    open_session: OpenSession = background_session, *, interval: float = FLUSH_INTERVAL_S
) -> None:
    """기록을 모으기 시작하고 `interval` 마다 쓴다. 취소되면 모으기를 멈추고 남은 것을 쓴다."""
    llm_usage.enable()
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await flush(open_session)
            except Exception as exc:
                # flush 는 예외를 내지 않는 게 약속이지만, 버그 하나로 기록이 프로세스 수명 동안
                # 조용히 꺼지면 BM 데이터에 구멍이 생긴다 — 알리고 계속 돈다
                _report_failure(exc, what="writer 가 예외를 받았다")
    finally:
        llm_usage.disable()
        try:
            await asyncio.wait_for(flush(open_session), timeout=FINAL_FLUSH_TIMEOUT_S)
        except TimeoutError:
            log.warning("llm usage 마지막 저장이 %.0f초 안에 끝나지 않았다", FINAL_FLUSH_TIMEOUT_S)
