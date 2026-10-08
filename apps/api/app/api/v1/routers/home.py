"""홈 — #259 (03 홈).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 (deps/child.py). 동의는 요청마다 확인하지 않는다 — 계약이 쓰기에만 걸어 두었고,
화면은 `GET /me` 의 `consent_required` 로 막는다.

🚨 건강 기록은 첫 배포 범위 밖이라 세지 않는다 (#259).
🚨 모델을 부르지 않는다. 하이라이트 문구와 예상 질문은 규칙이다.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter

from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ErrorEnvelope
from app.api.quota import KST
from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.home import (
    AgentPromptOut,
    HighlightOut,
    HomeResponse,
    TodayEventOut,
)
from app.domains.child.models import Child
from app.domains.memory.observation.models import ObservationStatus
from app.domains.memory.observation.repository import (
    count_active_observations,
    list_active_refs_by_affinity,
)
from app.domains.memory.profile.models import ProfileAffinity, ProfileState
from app.domains.memory.profile.repository import list_affinities
from app.domains.schedule.repository import list_events
from app.rules.affinity_reason import is_stale, state_reason
from app.rules.home_prompts import agent_prompts

router = APIRouter()

_UPCOMING_DAYS = 7
# 기록 탭 목록과 같은 기준 — 고친 기록도 센다. deleted 만 빠진다.
_COUNTED_STATUSES = (
    ObservationStatus.ACTIVE,
    ObservationStatus.STAND_ALONE,
    ObservationStatus.INACTIVE,
)


@router.get(
    "/children/{cid}/home",
    responses={403: {"model": ErrorEnvelope}},
)
async def get_home(child: AccessibleChild, session: SessionDep) -> HomeResponse:
    """홈 화면 한 번에. 숫자 셋 · 오늘 일정 · 눈여겨볼 기억 · 시간대 예상 질문."""
    now = datetime.now(KST)
    today = now.date()
    week_start = today - timedelta(days=today.weekday())

    counts = await count_active_observations(
        session,
        child_id=child.child_id,
        period_start=week_start,
        period_end=week_start + timedelta(days=7),
        exclude_health=True,
        statuses=_COUNTED_STATUSES,
    )
    day_start = datetime.combine(today, datetime.min.time(), tzinfo=KST)
    todays = await list_events(
        session,
        child_id=child.child_id,
        starts_before=day_start + timedelta(days=1),
        ends_after=day_start,
    )
    upcoming = await list_events(
        session,
        child_id=child.child_id,
        starts_before=now + timedelta(days=_UPCOMING_DAYS),
        ends_after=now,
    )
    nickname = (await session.get(Child, child.child_id)).nickname

    return HomeResponse(
        observation_count=counts.total_count,
        week_count=counts.period_count,
        upcoming_count=len(upcoming),
        today=[TodayEventOut(event_id=str(e.id), title=e.title) for e in todays],
        highlight=await _highlight(session, child_id=child.child_id, today=today),
        agent_prompts=[
            AgentPromptOut(agent=p.agent, text=p.text) for p in agent_prompts(nickname, now)
        ],
    )


async def _highlight(session: SessionDep, *, child_id, today) -> HighlightOut | None:
    """확인된 기억 중 마지막 기록이 가장 최근인 것. 없으면 후보에서 같은 기준으로.

    6개월 낡은 기억(NF-08)과 묶인 active 기록이 없는 기억은 고르지 않는다 — 기억 목록과 같은 기준.
    """
    rows = await list_affinities(session, child_id=child_id)  # 마지막 기록이 최근인 것이 앞
    refs = await list_active_refs_by_affinity(
        session, child_id=child_id, affinity_ids=[r.id for r in rows]
    )
    usable = [r for r in rows if refs.get(r.id) and not is_stale(r.last_observed_on, today=today)]
    for state in (ProfileState.CONFIRMED, ProfileState.CANDIDATE):
        picked = next((r for r in usable if r.state == state), None)
        if picked:
            return _highlight_out(picked, count=len(refs[picked.id]), today=today)
    return None


def _highlight_out(row: ProfileAffinity, *, count: int, today) -> HighlightOut:
    return HighlightOut(
        text=row.merge_key,
        state_reason=state_reason(
            observation_count=count, last_observed_on=row.last_observed_on, today=today
        ),
        ref=Ref(kind="profile_affinity", id=str(row.id)),
    )
