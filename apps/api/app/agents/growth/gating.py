"""Growth 의 월령 게이트 값.

Growth 는 tool 별 `min_month` 눈금이다 — 열리는 시점만 다르고 배타적인 tool 이 없다
(docs/agents/shared/연령별_Tool_전략.md §4, Growth_Tool_명세.md §1). `growth_review` 에는 연령 축이
없다 — 성장 추이는 월령과 무관하게 측정 로그 전부를 읽는다.

라벨 안에서 코드가 정하는 값(루틴 모드 · 허용 카테고리)도 여기 둔다. registry 와 tool 이 같이
읽는 값이라 따로 둔다 — registry 는 tool 을 import 하므로 값을 registry 에 두면 tool 쪽에서 되돌아
import 할 수 없다.

TODO: reference/age_gates.yaml 이 생기면 숫자를 거기서 읽는다 (공통_구현_계획 §4-2).
      그 전까지는 여기가 Growth 의 유일한 자리다.
"""

from typing import Literal

from app.agents.common.gate import Gate

EDUCATION_MIN_MONTH = 12  # 교육 tool 이 열린다. 그 아래는 놀이가 곧 배움이라 Activity 로 안내한다
STEP_MIN_MONTH = 12  # 루틴이 안내 한 줄(rhythm_info)에서 다음 단계 제안(next_step)으로 넘어간다
MANNER_MIN_MONTH = 24  # 예절 카테고리 (G-7, 설정값으로 시작)
HABIT_MIN_MONTH = (
    36  # 습관 교정 카테고리 (G-7, 설정값으로 시작). growth_doc 의 CHECK 와 같은 값이다
)

# 모델에게 보이는 tool, 열리는 순서. tool → 열리는 월령. 표에 없는 tool 은 열지 않는다
MIN_MONTH: dict[str, int] = {
    "search_education_memory": EDUCATION_MIN_MONTH,
    "search_routine_memory": 0,
    "search_activity_memory": 0,
    "search_affinity": 0,
    "lookup_notice": EDUCATION_MIN_MONTH,
    "search_books": 0,
    "propose_learning_activity": EDUCATION_MIN_MONTH,
    "propose_routine_plan": 0,
    "propose_books": 0,
}

# 모델에게 보이지 않는 코드 tool. 모델이 부르면 스스로 통과시킬 수 있는 것들이다
# (docs/agents/growth/Growth_Agent_명세.md §5). 코드가 정해진 지점에서 직접 부른다
CODE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "compute_growth_delta",
        "rank_evidence",
        "check_routine_category",
        "search_growth_doc",
        "pick_next_step",
    }
)

# 기관 공지가 여닫는 tool. 공지가 없다고 추천이 막히지는 않는다(이 tool만 빠진다)
NOTICE_TOOL = "lookup_notice"

RoutineMode = Literal["rhythm_info", "next_step", "habit_fix"]


def opens(name: str, gate: Gate) -> bool:
    """이 tool 이 이 gate 에서 열리는가. 월령과 공지 유무만 본다 — 닫힘은 registry 가 따로 본다."""
    min_month = MIN_MONTH.get(name)
    if min_month is None or gate.stage.months < min_month:
        return False
    if name == NOTICE_TOOL:
        return gate.data.notice
    return True


def routine_mode(months: int, category: str | None) -> RoutineMode:
    """`propose_routine_plan` 이 어느 모드로 도는가.

    0–11개월은 카테고리와 상관없이 `rhythm_info` 다 — 안내 한 줄이고 추천 카드는 없다.
    12개월부터는 습관(`habit`)이면 `habit_fix`, 그 밖은 `next_step` 이다.
    허용 여부는 `check_routine_category` 가 따로 본다 (습관은 36개월부터).
    """
    if months < STEP_MIN_MONTH:
        return "rhythm_info"
    if category == "habit":
        return "habit_fix"
    return "next_step"
