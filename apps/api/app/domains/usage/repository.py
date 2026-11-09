"""LLM 사용량 저장소 (#710). writer(`api/llm_usage_writer.py`)가 묶음으로 넣는다.

여기서는 commit 하지 않는다 — 커밋은 부른 쪽이다.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_usage import UsageRecord
from app.domains.usage.models import LlmUsage

RETENTION_PERIOD = timedelta(days=90)
"""90일. 처리방침 "인공지능 요청 기록 3개월" · retention-v1 서버 로그와 같은 값이다 (#710).

탈퇴해도 지우지 않고 보호자 칸만 비우므로(SET NULL) 이 기간이 끝나야 숫자가 사라진다.
"""

_INSERT_SQL = text(Path(__file__).with_name("insert_usage.sql").read_text(encoding="utf-8"))


async def insert_usage(session: AsyncSession, records: Sequence[UsageRecord]) -> int:
    """묶음을 한 문장으로 넣고 넣은 행 수를 돌려준다. 빈 묶음이면 DB 를 부르지 않고 0.

    🚨 없는 보호자(탈퇴)의 기록도 넣는다 — 그 행은 `parent_id` 가 NULL 이다.
       그래서 돌려주는 수는 늘 `len(records)` 다.
    """
    if not records:
        return 0
    result = await session.execute(
        _INSERT_SQL,
        {
            "occurred_at": [r.occurred_at for r in records],
            "parent_id": [r.parent_id for r in records],
            "run_id": [r.run_id for r in records],
            "source": [r.source for r in records],
            "role": [r.role for r in records],
            "provider": [r.provider for r in records],
            "model": [r.model for r in records],
            "prompt_tokens": [r.prompt_tokens for r in records],
            "cached_prompt_tokens": [r.cached_prompt_tokens for r in records],
            "completion_tokens": [r.completion_tokens for r in records],
            "reasoning_tokens": [r.reasoning_tokens for r in records],
            "latency_ms": [r.latency_ms for r in records],
        },
    )
    return result.rowcount


async def purge_expired_llm_usage(session: AsyncSession, *, now: datetime) -> int:
    """보관 기간이 끝난 사용량을 지우고 지운 행 수를 돌려준다. flush 까지 — 커밋은 호출자다.

    현재 시각은 호출자가 넘긴다 — `purge_expired_location_logs` 와 같은 이유
    (테스트가 실제 시각에 묶이지 않게).
    탈퇴한 보호자의 행(`parent_id` NULL)도 같은 기준으로 지운다.
    """
    result = await session.execute(
        delete(LlmUsage).where(LlmUsage.occurred_at <= now - RETENTION_PERIOD)
    )
    await session.flush()
    return result.rowcount
