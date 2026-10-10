"""`routine_coaching` 의 코드 쪽 — 모델이 하지 않는 판정들.

    check_routine_category   연령 허용표. 불허면 해당 readout 으로 교체한다
    routine_gap              근거(같은 카테고리 · 14일)가 없거나 습관 `trigger` 가 비면 되묻는다
    current_assistance_level 14일 창 안 관찰에서 지금 도움 수준을 고른다
    pick_next_step           `next_step_of` 사슬에서 **바로 다음 칸**을 고른다

모델은 `pick_next_step` 이 고른 행을 집 상황에 맞춰 문장으로 옮길 뿐이다 — 단계를 고르지 않는다.
건너뛰면 아이가 아직 못 하는 걸 시키게 된다 (Growth_Tool_명세.md §3).

`routine_coaching` 은 근거가 없을 때 **일반 추천을 내지 않는다.** 아이가 지금 어디까지 하는지
모르는 채 내놓는 조언은 어느 집에나 해당하는 일반론이다. 대신 되묻는다 (Growth_Agent_명세.md §6).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from app.agents.common.evidence import OBSERVATION_WINDOW_DAYS
from app.agents.growth.gating import HABIT_MIN_MONTH, MANNER_MIN_MONTH
from app.agents.growth.readouts import (
    ASK_HABIT_CURRENT,
    ASK_HABIT_TRIGGER,
    ASK_ROUTINE_CURRENT,
    CLOSED_HABIT_UNDER36,
    CLOSED_MANNER_UNDER24,
    NEXT_STEP_CHAIN_END,
    READOUTS,
)
from app.agents.growth.store.ports import GrowthDocRow, RoutineObservation
from app.rules.term_match import normalize

# 도움이 많이 필요한 쪽 → 혼자 하는 쪽. 사슬은 이 순서로 나아간다
ASSISTANCE_ORDER: tuple[str, ...] = (
    "full_assist",
    "partial_assist",
    "verbal_prompt",
    "independent",
)
# growth_doc 의 자립 단계 행이 어느 도움 수준에 해당하는지 `tags` 에 `assistance:<수준>` 으로 적는다
ASSISTANCE_TAG_PREFIX = "assistance:"


class ChainError(ValueError):
    """`next_step_of` 사슬이 끊겼거나 갈라졌거나 돈다. 추측해서 고르지 않고 올린다.

    적재 검사가 먼저 막으므로 run 중에는 나오지 않아야 한다.(TODO)
    """


def check_routine_category(category: str, months: int) -> str | None:
    """연령 허용표 대조. 불허면 readout 키, 허용이면 None.

    예절은 24개월부터, 습관 교정은 36개월부터다 (G-7, 설정값). 0–11개월 `rhythm_info` 모드는
    카테고리를 보기 전에 정해지지만, 이 판정이 먼저 닫으면 교정 대신 지켜보라는 안내가 나간다.
    """
    if category == "habit" and months < HABIT_MIN_MONTH:
        return CLOSED_HABIT_UNDER36
    if category == "social_manner" and months < MANNER_MIN_MONTH:
        return CLOSED_MANNER_UNDER24
    return None


def routine_evidence(
    cited: Sequence[RoutineObservation], *, category: str, today: date
) -> tuple[RoutineObservation, ...]:
    """근거로 세는 루틴 관찰 — **선언한 카테고리와 같고 최근 14일 안**인 것만.

    창은 공통 티어 3(`rank_evidence`)과 같다. affinity 는 관심사를 잇는 데만 쓰고
    여기에 넣지 않는다.
    """
    window_start = today - timedelta(days=OBSERVATION_WINDOW_DAYS)
    return tuple(
        obs for obs in cited if obs.routine_category == category and obs.observed_on >= window_start
    )


def routine_gap(cited: Sequence[RoutineObservation], *, category: str, today: date) -> str | None:
    """되물어야 하면 `ask.*` 키, 근거가 충분하면 None. 한 번에 질문 하나만 나간다.

    - 근거 0 → 습관은 `ask.habit_current`, 그 밖은 `ask.routine_current`
      (카테고리와 무관하게 되묻는다)
    - 습관인데 근거 관찰에 `trigger` 가 하나도 없음 → `ask.habit_trigger`
      언제 나오는 행동인지 모르면 교정안이 아이와 상관없는 일반론이 된다
    """
    evidence = routine_evidence(cited, category=category, today=today)
    if category == "habit":
        if not evidence:
            return ASK_HABIT_CURRENT
        if not any(obs.trigger and obs.trigger.strip() for obs in evidence):
            return ASK_HABIT_TRIGGER
        return None
    return None if evidence else ASK_ROUTINE_CURRENT


def ask_question(key: str, subject: str) -> str:
    """`ask.*` 키를 되묻기 한 줄로. `subject` 는 코드가 만든 행동 이름이어야 한다."""
    return READOUTS.render(key, subject=subject).body


def current_assistance_level(
    observations: Sequence[RoutineObservation],
    *,
    category: str,
    subject: str | None,
    today: date,
) -> str | None:
    """14일 창 안, 같은 카테고리(와 같은 행동 이름)의 관찰에서 **가장 최근** 도움 수준을 고른다.

    `assistance_level` 이 비어 있는 관찰은 건너뛴다 — 추정하지 않는다. 하나도 없으면 None 이고
    호출부는 `ask.routine_current` 로 되묻는다. 오래된 수준으로 단계를 고르면 이미 넘어선 단계를
    시킬 수 있다. 같은 날 둘이면 도움이 더 필요한 쪽을 고른다 — 건너뛰지 않는 쪽이다.
    """
    candidates = [
        obs
        for obs in routine_evidence(observations, category=category, today=today)
        if obs.assistance_level in ASSISTANCE_ORDER
        and (subject is None or normalize(obs.subject) == normalize(subject))
    ]
    if not candidates:
        return None
    latest = max(obs.observed_on for obs in candidates)
    same_day = [obs for obs in candidates if obs.observed_on == latest]
    return min(
        (obs.assistance_level for obs in same_day if obs.assistance_level),
        key=ASSISTANCE_ORDER.index,
    )


@dataclass(frozen=True)
class NextStep:
    """`pick_next_step` 결과. `row` 가 있으면 모델이 그 행을 문장으로 옮기고,
    없으면 readout 이다."""

    row: GrowthDocRow | None = None
    readout_key: str | None = None  # next_step.chain_end · ask.routine_current

    def __post_init__(self) -> None:
        if (self.row is None) == (self.readout_key is None):
            raise ValueError("다음 칸이 있거나 readout 이 있거나 둘 중 하나다")


def ordered_chain(rows: Sequence[GrowthDocRow]) -> tuple[GrowthDocRow, ...]:
    """`next_step_of`(앞 단계)로 사슬을 처음부터 끝까지 세운다. 이상하면 `ChainError`.

    처음은 앞 단계가 없는 행 하나여야 한다. 갈라지거나(한 행을 가리키는 행이 둘) 끊기거나(처음이
    둘) 돌면 올린다. 적재 검사가 같은 함수로 시드를 막게 한다.
    """
    if not rows:
        raise ChainError("사슬이 비었다")
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(rows):
        raise ChainError("사슬에 같은 행이 둘 있다")
    successor: dict[UUID, GrowthDocRow] = {}
    roots: list[GrowthDocRow] = []
    for row in rows:
        if row.next_step_of is None or row.next_step_of not in by_id:
            roots.append(row)
            continue
        if row.next_step_of in successor:
            raise ChainError("사슬이 갈라진다 — 한 행을 앞 단계로 가리키는 행이 둘이다")
        successor[row.next_step_of] = row
    if len(roots) != 1:
        raise ChainError(f"사슬의 처음이 {len(roots)}개다")
    ordered = [roots[0]]
    while ordered[-1].id in successor:
        ordered.append(successor[ordered[-1].id])
        if len(ordered) > len(rows):
            raise ChainError("사슬이 돈다")
    if len(ordered) != len(rows):
        raise ChainError("사슬에서 떨어진 행이 있다")
    return tuple(ordered)


def _level_of(row: GrowthDocRow) -> str | None:
    levels = [
        t[len(ASSISTANCE_TAG_PREFIX) :] for t in row.tags if t.startswith(ASSISTANCE_TAG_PREFIX)
    ]
    if not levels:
        return None
    if len(levels) > 1 or levels[0] not in ASSISTANCE_ORDER:
        raise ChainError(f"도움 수준 태그가 이상하다: {row.doc_key}")
    return levels[0]


def pick_next_step(chain: Sequence[GrowthDocRow], *, assistance_level: str | None) -> NextStep:
    """사슬에서 **현재 칸의 바로 다음 행**을 고른다. 건너뛰지 않는다.

    - `assistance_level` 이 비면 추정하지 않고 되묻는다 (`ask.routine_current`).
    - 현재 칸 = 그 도움 수준 태그가 붙은 **첫** 행. 같은 수준 행이 여럿이면 가장 앞에서 시작해
      다음 칸을 한 칸씩 올린다. 그 수준 행이 사슬에 없으면 그보다 낮은 수준의 마지막 행이다.
      아이가 사슬의 첫 행보다도 아래에 있으면 첫 행이 다음 칸이다.
    - 현재 칸이 사슬의 끝이면 `next_step.chain_end` — 없는 다음 칸을 지어내지 않는다.
    """
    if not assistance_level:
        return NextStep(readout_key=ASK_ROUTINE_CURRENT)
    if assistance_level not in ASSISTANCE_ORDER:
        raise ValueError(f"알 수 없는 도움 수준: {assistance_level}")

    ordered = ordered_chain(chain)
    levels = [_level_of(row) for row in ordered]
    if any(level is None for level in levels):
        raise ChainError("도움 수준 태그가 없는 행이 있다")
    ranks = [ASSISTANCE_ORDER.index(level) for level in levels if level]
    if ranks != sorted(ranks):
        raise ChainError("사슬이 도움이 많이 필요한 쪽에서 혼자 하는 쪽으로 나아가지 않는다")

    target = ASSISTANCE_ORDER.index(assistance_level)
    if target in ranks:
        current = ranks.index(target)
    else:
        current = max((i for i, rank in enumerate(ranks) if rank < target), default=-1)
    following = current + 1
    if following >= len(ordered):
        return NextStep(readout_key=NEXT_STEP_CHAIN_END)
    return NextStep(row=ordered[following])
