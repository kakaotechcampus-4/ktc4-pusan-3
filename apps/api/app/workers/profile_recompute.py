"""기억 상태를 하루 한 번 다시 계산하는 배치의 진입점 — 서버 cron 이 부른다 (#636)

기억(`profile_affinity`)의 상태는 "오늘 기준 최근 N일" 로 정해진다 (rules/profile.py) — 14일 안의
관찰 일수(O), 21일 안의 wrong(W), 마지막 관찰 뒤 21일. 그런데 `recompute_profile` 은 관찰이
붙거나 고쳐질 때와 Correction 때만 불린다. 새 기록이 없으면 날짜가 지나도 저장된 상태가 그대로다.
이 배치가 그 빠진 시간 트리거다. 공식과 재계산은 그대로 부르기만 한다.

  · 대상 — 지워지지 않은 아이의 archived 가 아닌 기억 전부.
    # ponytail: 전체 순회. 아이당 기억이 수십 개라 가볍다. 느려지면 #636 의 "막 창을 벗어난 것"
    #   쿼리 3개 + 인덱스로 줄인다
  · archived 는 다시 보지 않는다 — 시간만으로 archived 에서 나올 수 없다(새 관찰이 있어야 하고,
    그때는 Curator 가 재계산한다).
  · 아이마다 Curator 와 같은 아이 잠금(lock_child_memory)을 잡고 아이마다 커밋한다 —
    같은 시각에 새 기록이 들어와도 재계산이 엇갈리지 않고, 한 아이가 실패해도 나머지는 반영된다.

🚨 로그에는 건수만 남긴다. 실패는 ERROR 로 찍혀 Discord 로 간다.

  서버 cron 한 줄과 확인 방법은 apps/api/README.md "기억 상태 다시 계산".
  로컬 한 번: cd apps/api && uv run python -m app.workers.profile_recompute
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alerts import configure_alerts
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.domains.child.models import Child
from app.domains.memory.profile.models import ProfileAffinity, ProfileState
from app.domains.memory.profile.service import lock_child_memory, recompute_profile
from app.infra.db.session import async_session_factory, engine
from app.integrations import discord

# cleanup.py 와 같은 이유로 __name__ 이 아니다 — `python -m` 이면 app.* 밖으로 빠져 알림이 안 간다
log = logging.getLogger("app.workers.profile_recompute")

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class RecomputeResult:
    children: int
    profiles: int
    changed: int
    failed_children: int


async def _targets(session: AsyncSession) -> dict[uuid.UUID, list[uuid.UUID]]:
    rows = await session.execute(
        select(ProfileAffinity.child_id, ProfileAffinity.id)
        .join(Child, Child.id == ProfileAffinity.child_id)
        .where(ProfileAffinity.state != ProfileState.ARCHIVED, Child.deleted_at.is_(None))
        .order_by(ProfileAffinity.child_id)
    )
    by_child: dict[uuid.UUID, list[uuid.UUID]] = {}
    for child_id, profile_id in rows:
        by_child.setdefault(child_id, []).append(profile_id)
    return by_child


async def _recompute_child(
    session: AsyncSession, child_id: uuid.UUID, profile_ids: list[uuid.UUID], *, today: date
) -> tuple[int, int]:
    """아이 한 명 — 잠그고, 기억마다 다시 계산하고, 커밋한다. (다시 계산한 수, 바뀐 수)."""
    await lock_child_memory(session, child_id)
    profiles = changed = 0
    for profile_id in profile_ids:
        # 행 잠금은 recompute_profile 이 잡는다. 여기 읽는 상태는 "바뀜" 건수용이라 잠그지 않는다
        before = await session.scalar(
            select(ProfileAffinity.state).where(ProfileAffinity.id == profile_id)
        )
        if before is None:  # 대상을 고른 뒤 지워졌다
            continue
        after = (await recompute_profile(session, profile_id=profile_id, today=today)).state
        profiles += 1
        changed += before != after
    await session.commit()
    return profiles, changed


async def run_recompute(session: AsyncSession, *, today: date) -> RecomputeResult:
    """대상 기억을 아이 단위로 다시 계산하고 아이마다 커밋한다.

    기준일은 호출자가 넘긴다 — 테스트가 실제 날짜에 묶이지 않게.
    `recompute_profile` 이 멱등이라 같은 날 두 번 돌려도 결과가 같다.
    """
    targets = await _targets(session)
    profiles = changed = failed = 0
    for child_id, profile_ids in targets.items():
        try:
            done, moved = await _recompute_child(session, child_id, profile_ids, today=today)
            profiles += done
            changed += moved
        except Exception:
            await session.rollback()
            failed += 1
            log.exception("기억 상태 다시 계산 실패 (아이 1명)")
    log.info(
        "기억 상태 다시 계산: 아이 %d명 · 기억 %d건 · 바뀜 %d건 · 실패 아이 %d명",
        len(targets),
        profiles,
        changed,
        failed,
    )
    return RecomputeResult(
        children=len(targets), profiles=profiles, changed=changed, failed_children=failed
    )


async def _main() -> int:
    try:
        async with async_session_factory() as session:
            result = await run_recompute(session, today=datetime.now(KST).date())
    finally:
        await engine.dispose()
    return 1 if result.failed_children else 0


if __name__ == "__main__":
    configure_logging()
    if settings.alert_webhook_url:
        configure_alerts(discord.webhook_sender(settings.alert_webhook_url), env=settings.APP_ENV)
    try:
        code = asyncio.run(_main())
    except Exception:
        log.exception("기억 상태 다시 계산 실패")
        raise SystemExit(1) from None
    raise SystemExit(code)
