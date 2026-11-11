"""홈 — #259 (03 홈).

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 본문보다 먼저
확인한다 (deps/child.py). 동의는 요청마다 확인하지 않는다 — 계약이 쓰기에만 걸어 두었고,
화면은 `GET /me` 의 `consent_required` 로 막는다.

건강 기록도 기록 탭처럼 센다 (#764).
🚨 모델을 부르지 않는다. 하이라이트 문구와 예상 질문은 규칙이다.
🚨 오늘 급식은 저장된 그대로 보여 주기만 한다 — 알레르기 판정 · 추천을 붙이지 않고, 메뉴 글자를
   로그에 남기지 않는다 (루트 CLAUDE.md §2).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter

from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ErrorEnvelope
from app.api.quota import KST
from app.api.v1.routers.observations import LISTED_STATUSES
from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.home import (
    AgentPromptOut,
    HighlightOut,
    HomeResponse,
    TodayEventOut,
    TodayMealOut,
)
from app.domains.child.models import Child
from app.domains.daycare_meal.models import DaycareMeal, DaycareOrigin, DaycareSlot
from app.domains.daycare_meal.repository import list_daycare_meals
from app.domains.memory.observation.repository import (
    count_active_days_by_affinity,
    count_active_observations,
    list_active_refs_by_affinity,
)
from app.domains.memory.profile.models import ProfileAffinity, ProfileState
from app.domains.memory.profile.repository import list_affinities
from app.domains.schedule.repository import list_events
from app.rules.affinity_reason import is_stale, state_reason
from app.rules.home_prompts import agent_prompts
from app.rules.profile import CANDIDATE_MIN_DAYS

router = APIRouter()

_UPCOMING_DAYS = 7

# 하루 안의 끼니 순서와 이름 — Food 의 급식 조회 문구(agents/food `DAYCARE_SLOT_ORDER` ·
# `daycare_lookup.SLOT_LABEL`)와 같은 값이다. api 는 agents 내부를 import 하지 않아 따로 둔다.
_SLOT_ORDER = {DaycareSlot.SNACK_AM: 0, DaycareSlot.LUNCH: 1, DaycareSlot.SNACK_PM: 2}
_SLOT_LABEL = {
    DaycareSlot.SNACK_AM: "오전 간식",
    DaycareSlot.LUNCH: "점심",
    DaycareSlot.SNACK_PM: "오후 간식",
}
# 표준식단 문구는 Food 의 `daycare.reference_menu` 와 같은 사실이다 — 아이가 실제로 먹은 것이 아니다
_ORIGIN_LABEL = {
    DaycareOrigin.OCR: "기관 급식표",
    DaycareOrigin.CENTER_STANDARD: "지역 표준식단 · 실제 급식과 다를 수 있어요",
}


@router.get(
    "/children/{cid}/home",
    responses={403: {"model": ErrorEnvelope}},
)
async def get_home(child: AccessibleChild, session: SessionDep) -> HomeResponse:
    """홈 화면 한 번에. 숫자 셋 · 오늘 급식과 일정 · 눈여겨볼 기억 · 시간대 예상 질문."""
    now = datetime.now(KST)
    today = now.date()
    week_start = today - timedelta(days=today.weekday())

    counts = await count_active_observations(
        session,
        child_id=child.child_id,
        period_start=week_start,
        period_end=week_start + timedelta(days=7),
        statuses=LISTED_STATUSES,  # 기록 탭 목록과 같은 기준 — deleted 만 빠진다
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
    meals = await list_daycare_meals(
        session, child_id=child.child_id, date_from=today, date_to=today
    )
    nickname = (await session.get(Child, child.child_id)).nickname

    return HomeResponse(
        observation_count=counts.total_count,
        week_count=counts.period_count,
        upcoming_count=len(upcoming),
        today=[
            *today_meals(meals),
            *(TodayEventOut(event_id=str(e.id), title=e.title) for e in todays),
        ],
        highlight=await _highlight(session, child_id=child.child_id, today=today),
        agent_prompts=[
            AgentPromptOut(agent=p.agent, text=p.text, prompt_key=p.prompt_key)
            for p in agent_prompts(nickname, now)
        ],
    )


def today_meals(rows: list[DaycareMeal]) -> list[TodayMealOut]:
    """그날 급식 행 → 끼니마다 한 줄. 오전 간식 → 점심 → 오후 간식 순이다.

    메뉴는 저장된 `menu_keys` 를 그대로 잇는다 — 보기 좋은 이름은 표에 없어서 지어내지 않는다.
    메뉴가 빈 끼니는 줄을 만들지 않는다 (빈 제목 줄 금지 · Food 의 급식 조회와 같은 규칙).
    NULL · 공백뿐인 키는 건너뛰고 앞뒤 공백은 다듬는다 — 한 행 때문에 홈 전체가 500 이 되지 않게.
    """
    lines = []
    for row in sorted(rows, key=lambda r: _SLOT_ORDER[DaycareSlot(r.meal_slot)]):
        menus = [m.strip() for m in row.menu_keys if m is not None and m.strip()]
        if not menus:
            continue
        slot = DaycareSlot(row.meal_slot)
        lines.append(
            TodayMealOut(
                title=f"{_SLOT_LABEL[slot]} · {', '.join(menus)}",
                origin=_ORIGIN_LABEL[DaycareOrigin(row.origin)],
            )
        )
    return lines


async def _highlight(session: SessionDep, *, child_id, today) -> HighlightOut | None:
    """확인된 기억 중 마지막 기록이 가장 최근인 것. 없으면 후보에서 같은 기준으로.

    6개월 낡은 기억(NF-08)과 묶인 active 기록이 없는 기억은 고르지 않는다 — 기억 목록과 같은 기준.
    후보는 묶인 기록이 서로 다른 날 `CANDIDATE_MIN_DAYS` 이상일 때만 — 추천 근거(티어 2)와 같은
    기준이다. 하루치 관찰로 생긴 후보가 "요즘 ~를 좋아해요" 로 뜨지 않게 한다 (#626).
    카드의 "N번" 은 날이 아니라 기록 건수 그대로다.
    """
    rows = await list_affinities(session, child_id=child_id)  # 마지막 기록이 최근인 것이 앞
    ids = [r.id for r in rows]
    refs = await list_active_refs_by_affinity(session, child_id=child_id, affinity_ids=ids)
    days = await count_active_days_by_affinity(session, child_id=child_id, affinity_ids=ids)
    usable = [r for r in rows if refs.get(r.id) and not is_stale(r.last_observed_on, today=today)]
    for state, least in (
        (ProfileState.CONFIRMED, 1),
        (ProfileState.CANDIDATE, CANDIDATE_MIN_DAYS),
    ):
        picked = next((r for r in usable if r.state == state and days[r.id] >= least), None)
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
