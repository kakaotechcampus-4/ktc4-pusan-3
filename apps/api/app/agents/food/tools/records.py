"""기록 기반 tool 3개(식단 기억 검색 / 섭취 기록 분석 / 반복 메뉴 확인)"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.agents.common.evidence import (
    OBSERVATION_WINDOW_DAYS,
    ObservationRow,
    RankedEvidence,
    rank_evidence,
    top_refs,
)
from app.agents.food.amount_rules import amount_of
from app.agents.food.context import FoodContext
from app.agents.food.matching import related
from app.agents.food.nutrition_eval import read_meal_records
from app.agents.food.periods import period_range
from app.agents.food.result import ErrorCode, ToolResult, fail, ok
from app.agents.food.schemas.common import MealSlot
from app.agents.food.schemas.records import (
    AnalyzeMealRecordsArgs,
    CheckRepeatedMenusArgs,
    SearchFoodMemoryArgs,
)
from app.agents.food.store.ports import DAYCARE_SLOT_ORDER, DaycareSlot
from app.rules.affinity_reason import is_stale
from app.rules.menu_key import normalize_menu_key

# 몇 날 나오면 반복인지. 판정 기준은 코드의 상수로 둔다 — 모델이 정하지 않는다
REPEATED_MIN_DAYS = 3

# 급식 칸 → 모델에게 보이는 끼니. 간식 두 칸은 간식 하나로 본다
_DAYCARE_SLOT: dict[DaycareSlot, MealSlot] = {
    "lunch": MealSlot.LUNCH,
    "snack_am": MealSlot.SNACK,
    "snack_pm": MealSlot.SNACK,
}

_MEMORY = "search_food_memory"
# NO_RECORDS 의 다음 행동. 모델이 넣은 keywords 는 싣지 않는다 — message 는 로그로 흘러간다
_NO_MATCH = (
    "그 이름의 식사 기록은 없다. keywords 를 비우고 다시 찾거나, 근거 없이 일반 추천을 낸다."
)
_NO_RECORDS = "식사 기록이 없다. evidence 를 비우고 일반 추천을 낸다. 보호자에게 되묻지 않는다."


async def search_food_memory(context: FoodContext, args: SearchFoodMemoryArgs) -> ToolResult:
    """아이의 식사 기록과 음식 선호·기피 정보를 근거 순서대로 돌려준다.

    - `args.period` 라벨은 코드가 날짜로 푼다. 비우면 최근 14일(`OBSERVATION_WINDOW_DAYS`)이다.
      profile_affinity(domain=food)는 기간과 상관없이 읽는다 — 장기 신호의 증류라서다.
    - `common/evidence.rank_evidence()` 로 줄 세운다. 순서를 모델에게 맡기지 않는다. 기피(−1)도
      근거로 남는다 — 후보에서 지우는 것은 안전 필터의 일이다.
    - 같은 음식의 관찰은 가장 최근 1건만 남긴다 — 여러 날 먹은 딸기가 자리를 나눠 갖지 않게.
    - `keywords` 가 있으면 맞는 근거만 남긴다. 줄 세운 뒤에 거르므로 순서는 같다.
    - 상위 `TOP_N` 개만 돌려주고, 돌려준 것만 `context.state.seen_evidence` 에 적는다. 출력 tool 이
      이 표에 없는 id 를 인용하면 그 후보를 거절한다.
    - 마지막 관찰이 6개월 넘은 선호는 `is_stale=True` 다. 낡은 선호만으로 개인화 근거를 만들지
      않는다(루트 §2) — 그 판정은 출력 tool 이 한다.
    - 결과에는 kind · id · tier · label · polarity · 날짜 · is_stale 만 싣는다.
      raw_text 는 싣지 않는다.
    - 0행이면 NO_RECORDS. 호출부는 일반 추천으로 간다 — 추천을 질문으로 대신하지 않는다.
    """
    today = context.today
    if args.period is None:
        date_from, date_to = today - timedelta(days=OBSERVATION_WINDOW_DAYS), today
    else:
        date_from, date_to = period_range(args.period, today)

    memory = context.ports.memory
    observed = await memory.observations(
        child_id=context.child_id, date_from=date_from, date_to=date_to
    )
    affinities = await memory.affinities(child_id=context.child_id)

    ranked = rank_evidence(
        tuple(affinities),
        tuple(
            ObservationRow(
                id=row.id,
                kind="observation_food",
                subject=row.subject,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for row in observed
        ),
        today=today,
    )
    picked = top_refs(_matching(_latest_per_subject(ranked), args.keywords))
    if not picked:
        message = _NO_MATCH if args.keywords and ranked else _NO_RECORDS
        return fail("query", _MEMORY, ErrorCode.NO_RECORDS, message)

    context.state.seen_evidence.update(picked)
    return ok(
        "query",
        _MEMORY,
        records=[
            {
                "kind": item.ref.kind,
                "id": str(item.ref.id),
                "tier": item.tier,
                "label": item.label,
                "polarity": item.polarity,
                "observed_on": item.observed_on.isoformat(),
                "is_stale": item.ref.kind == "profile_affinity"
                and is_stale(item.observed_on, today=today),
            }
            for item in picked.values()
        ],
    )


def _latest_per_subject(ranked: tuple[RankedEvidence, ...]) -> tuple[RankedEvidence, ...]:
    """같은 음식 · polarity 의 관찰은 가장 최근 1건만. affinity 는 그대로 둔다.

    `rank_evidence` 가 같은 티어 안에서 최근 순으로 줄 세워 두어서 처음 나온 것이 가장 최근이다.
    """
    kept: list[RankedEvidence] = []
    seen: set[tuple[str, int]] = set()
    for item in ranked:
        if item.tier == 3:
            key = (normalize_menu_key(item.subject), item.polarity)
            if key in seen:
                continue
            seen.add(key)
        kept.append(item)
    return tuple(kept)


def _matching(
    ranked: tuple[RankedEvidence, ...], keywords: list[str]
) -> tuple[RankedEvidence, ...]:
    """keywords 중 하나라도 맞는 근거. keywords 가 비면 전부다.

    음식 이름과는 양쪽으로 본다(`matching.related`) — 모델은 "딸기우유" 처럼 길게, 기록은 "딸기"
    처럼 짧게 쓴다. 짧은 쪽이 두 글자 미만이면 같을 때만 맞는다.
    """
    wanted = [key for key in map(normalize_menu_key, keywords) if key]
    if not wanted:
        return ranked
    return tuple(
        item
        for item in ranked
        if any(related(key, normalize_menu_key(item.subject)) for key in wanted)
    )


async def analyze_meal_records(context: FoodContext, args: AnalyzeMealRecordsArgs) -> ToolResult:
    """기간 내 급식과 가정 식사 기록을 날짜별 한 목록으로 묶어 돌려준다.

    - `args.period` 라벨은 코드가 날짜로 푼다. 급식은 오늘 이전 것만 읽는다 — 오늘 급식은 앞으로
      나올 식단이다. 가정 식사는 오늘 것도 읽는다.
    - 읽는 코드는 영양 판정과 같다(`read_meal_records`). 그날 급식이 있으면 기관 공지에서 온
      관찰은 뺀다(N-10) — 알림장이 알려 준 점심과 급식표의 점심은 같은 식사다.
    - 날짜마다 먹은 메뉴와 출처(가정 · 급식), 끼니(급식만), 남겼는지(`left`)만 싣는다. 양은 숫자로
      싣지 않고 추정하지 않는다 — 남김은 양 사전에서 1 미만인 말(남김 · 반 그릇)과 급식 행의 양
      계수가 1 미만인 것이다. 사전에 없는 말은 남긴 것으로 보지 않는다.
    - 기록이 없는 날은 `recorded=False` 로 남긴다. 없는 날을 빼거나 채우지 않는다.
    - 가정 식사에는 끼니가 없다(`observation_food` 에 끼니 칸이 없다). `meal_slots` 를 고르면 끼니를
      아는 급식만 남기고, 걸러 낸 가정 기록 수를 `unslotted_omitted` 로 알린다.
    """
    today = context.today
    date_from, date_to = period_range(args.period, today)
    records = await read_meal_records(
        context, date_from=date_from, date_to=date_to, daycare_before=today
    )
    wanted = {MealSlot(slot) for slot in args.meal_slots}

    days: list[dict[str, Any]] = []
    omitted = 0
    total = 0
    for current in _each_day(date_from, date_to):
        meals: list[dict[str, Any]] = []
        for obs in records.observations.get(current, ()):
            total += 1
            if wanted:
                omitted += 1
                continue
            meals.append(
                {
                    "source": "home",
                    "slot": None,
                    "menu": obs.subject,
                    "left": amount_of(obs.amount_text).factor < 1.0,
                }
            )
        for row in sorted(
            records.daycare.get(current, ()), key=lambda r: DAYCARE_SLOT_ORDER[r.meal_slot]
        ):
            total += 1
            slot = _DAYCARE_SLOT[row.meal_slot]
            if wanted and slot not in wanted:
                continue
            meals.extend(
                {
                    "source": "daycare",
                    "slot": slot.value,
                    "menu": key,
                    "left": row.amount_factor < 1.0,
                }
                for key in row.menu_keys
            )
        days.append({"date": current.isoformat(), "recorded": bool(meals), "meals": meals})

    data: dict[str, Any] = {"days": days, "records": total}
    if wanted:
        data["unslotted_omitted"] = omitted
    return ok("query", "meal_records", **data)


async def check_repeated_menus(context: FoodContext, args: CheckRepeatedMenusArgs) -> ToolResult:
    """기간 내 반복해서 나온 메뉴를 돌려준다. 몇 번부터 반복인지는 코드가 정한다.

    - 급식과 가정 식사를 `analyze_meal_records` 와 같은 코드·같은 범위로 읽는다.
    - 메뉴 이름을 정규화해(`normalize_menu_key`) **서로 다른 날** 수를 센다. 같은 날 두 번 나온 것은
      하루다. `REPEATED_MIN_DAYS` 일 이상만 돌려준다.
    - 메뉴마다 몇 일인지, 마지막 날짜, 집 · 급식 어느 쪽에서 나왔는지(`sources`)를 싣는다.
    - 반복이 없으면 빈 목록이다 — 실패가 아니다. `records` 로 본 기록 수를 함께 준다.
    """
    today = context.today
    date_from, date_to = period_range(args.period, today)
    records = await read_meal_records(
        context, date_from=date_from, date_to=date_to, daycare_before=today
    )

    seen: dict[str, _MenuDays] = {}
    total = 0
    for current in records.days:
        for obs in records.observations.get(current, ()):
            total += 1
            _count(seen, obs.subject, current, "home")
        for row in records.daycare.get(current, ()):
            total += 1
            for key in row.menu_keys:
                _count(seen, key, current, "daycare")

    repeated = sorted(
        (item for item in seen.values() if len(item.days) >= REPEATED_MIN_DAYS),
        key=lambda item: (-len(item.days), item.menu),
    )
    return ok(
        "query",
        "repeated_menus",
        repeated=[
            {
                "menu": item.menu,
                "days": len(item.days),
                "last_on": max(item.days).isoformat(),
                "sources": sorted(item.sources),
            }
            for item in repeated
        ],
        records=total,
    )


@dataclass
class _MenuDays:
    menu: str  # 처음 나온 표기
    days: set[date] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)


def _count(seen: dict[str, _MenuDays], name: str, on: date, source: str) -> None:
    key = normalize_menu_key(name)
    if not key:
        return
    item = seen.setdefault(key, _MenuDays(menu=name))
    item.days.add(on)
    item.sources.add(source)


def _each_day(date_from: date, date_to: date) -> Iterator[date]:
    current = date_from
    while current <= date_to:
        yield current
        current += timedelta(days=1)
