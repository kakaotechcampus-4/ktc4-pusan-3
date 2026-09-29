"""Curator 연결 후 영향 받은 Profile 의 상태를 재계산한다.

link_observations 가 돌려준 affected_profile_ids 를 순회하며
last_observed_on 갱신 → recompute_profile 을 한다.

Curator(link_step) 안에서 부르지 않는다 — Curator 는 저장소 계약(CuratorStore)만
알고, profile 서비스 의존을 갖지 않는다. 이 함수를 호출하는 것은 실행 흐름(Phase C)이다.
"""

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.curator.embedding.linker import LinkResult
from app.domains.memory.profile.models import ProfileAffinity
from app.domains.memory.profile.repository import get_latest_active_observed_on
from app.domains.memory.profile.service import recompute_profile


async def recompute_after_linking(
    session: AsyncSession,
    *,
    result: LinkResult,
    today: date,
) -> None:
    """연결로 관찰이 늘어난 Profile 의 last_observed_on 과 상태를 재계산한다."""
    for pid_str in result.affected_profile_ids:
        pid = UUID(pid_str)
        profile = await session.get(ProfileAffinity, pid)
        if profile is None:
            continue

        latest = await get_latest_active_observed_on(
            session, affinity_id=pid, domain=profile.domain,
        )
        if latest is not None and latest != profile.last_observed_on:
            profile.last_observed_on = latest
            await session.flush()

        await recompute_profile(session, profile_id=pid, today=today)
