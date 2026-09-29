"""Activity 의 월령 · 위치 · 날씨 게이트 값.

Growth 와 같은 눈금 방식이다 — tool 마다 열리는 월령이 다르고 배타적인 tool 이 없다.
**Activity 에는 닫힌 조합이 없다.** 어느 월령에서도 놀이 추천은 나간다. 월령이 바꾸는 것은
근거를 어디서 모으느냐와 후보에 장소가 붙느냐뿐이다 (docs/agents/shared/연령별_Tool_전략.md §5-1).

registry 와 tool 이 같이 읽는 값이라 따로 둔다 — registry 는 tool 을 import 하므로
여기 값을 registry 에 두면 tool 쪽에서 되돌아 import 할 수 없다.

TODO: reference/age_gates.yaml 이 생기면 숫자를 거기서 읽는다 (공통_구현_계획 §4-2).
      그 전까지는 여기가 Activity 의 유일한 자리다.
"""

from app.agents.common.gate import Gate

# tool 이름 → 열리는 월령. 표에 없는 tool 은 열지 않는다 (기본값으로 열지 않는다)
MIN_MONTH: dict[str, int] = {
    "search_activity_memory": 0,
    "lookup_weather": 0,
    "lookup_schedule": 0,
    "search_nearby_places": 36,
    "propose_activity_candidates": 0,
}

# search_activity_memory 안에서 profile_affinity 를 읽기 시작하는 월령.
# 그 아래는 조회를 건너뛰고 관찰(티어 3)로만 근거를 만든다. tool 을 닫는 값이 아니다 —
# 18개월은 근거 모드를 가르지 않는다. 관찰이 있으면 그 아래도 personalized 가 나간다.
AFFINITY_MIN_MONTH = 18

# 위치와 날씨가 여닫는 tool. 월령과 별개로 둘 다 참이어야 열린다
PLACES_TOOL = "search_nearby_places"


def opens(name: str, gate: Gate) -> bool:
    """이 tool 이 이 gate 에서 열리는가."""
    min_month = MIN_MONTH.get(name)
    if min_month is None or gate.stage.months < min_month:
        return False
    if name == PLACES_TOOL:
        return gate.has_location and gate.outdoor_ok
    return True


def reads_affinity(gate: Gate) -> bool:
    """search_activity_memory 가 profile_affinity 를 읽을 월령인가."""
    return gate.stage.months >= AFFINITY_MIN_MONTH
