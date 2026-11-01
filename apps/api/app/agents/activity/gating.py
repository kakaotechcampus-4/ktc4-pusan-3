"""Activity 의 월령 · 위치 · 날씨 게이트 값.

Growth 와 같은 눈금 방식이다 — tool 마다 열리는 월령이 다르고 배타적인 tool 이 없다.
**월령 · 위치 · 날씨로 닫히는 조합은 없다.** 어느 월령에서도 놀이 추천은 나간다. 월령이 바꾸는
것은 근거를 어디서 모으느냐뿐이다. 장소 조회는 월령이 아니라 장소 어댑터로 여닫는다
(docs/agents/shared/연령별_Tool_전략.md §5-1). 위치가 없으면 보호자가 말한 지역으로 찾는다.
알레르기 조회 실패로 전부 닫히는 것은 registry 의 `closed_readout_key` 가 따로 본다 (D7).

registry 와 tool 이 같이 읽는 값이라 따로 둔다 — registry 는 tool 을 import 하므로
여기 값을 registry 에 두면 tool 쪽에서 되돌아 import 할 수 없다.

월령 숫자는 `reference/age_gates.yaml` 이 정본이다 — 여기서는 읽기만 한다 (#372).
tool 이 열리는 월령은 전부 0 이라(항상 열림) 표만 두고, 0 이 아닌 값만 파일에서 읽는다.
"""

from app.agents.activity.weather import WeatherBrief
from app.agents.common.gate import Gate
from app.agents.common.reference import age_gates

# tool 이름 → 열리는 월령. 표에 없는 tool 은 열지 않는다 (기본값으로 열지 않는다)
MIN_MONTH: dict[str, int] = {
    "search_activity_memory": 0,
    "lookup_weather": 0,
    "lookup_schedule": 0,
    "search_nearby_places": 0,
    "propose_activity_candidates": 0,
}

# search_activity_memory 안에서 profile_affinity 를 읽기 시작하는 월령.
# 그 아래는 조회를 건너뛰고 관찰(티어 3)로만 근거를 만든다. tool 을 닫는 값이 아니다 —
# 근거 모드를 가르지 않는다. 관찰이 있으면 그 아래도 personalized 가 나간다.
# Curator 도 같은 값으로 그 아래 아이의 프로필을 만들지 않는다 — 여기는 방어선이다 (age_gates.yaml).
AFFINITY_MIN_MONTH = age_gates().affinity_min_month

# 장소 어댑터가 여닫는 tool. 날씨가 나쁘면 닫지 않고 실내 종류로 좁힌다 — 좁히는 것은 tool 이 한다.
# 위치가 없어도 연다 — 보호자가 말한 지역으로 찾는다(`areas.py`)
PLACES_TOOL = "search_nearby_places"
# 위치와 날씨 어댑터가 둘 다 있어야 여는 tool. 없으면 부를 때마다 실패만 돌려준다
WEATHER_TOOL = "lookup_weather"


def opens(name: str, gate: Gate) -> bool:
    """이 tool 이 이 gate 에서 열리는가."""
    min_month = MIN_MONTH.get(name)
    if min_month is None or gate.stage.months < min_month:
        return False
    if name == PLACES_TOOL:
        return finds_places(gate)
    if name == WEATHER_TOOL:
        return checks_weather(gate)
    return True


def finds_places(gate: Gate) -> bool:
    """장소를 찾을 수 있는가 — 장소 어댑터가 있어야 한다. 좌표가 없으면 보호자가 말한 지역으로만
    찾는다 — 지역도 없으면 tool 이 INVALID_ARGS 로 돌려보낸다."""
    return gate.data.places


def checks_weather(gate: Gate) -> bool:
    """날씨를 조회할 수 있는가 — 좌표와 날씨 어댑터가 둘 다 있어야 한다. 없으면 부르지 않은
    것이라 실패(`degraded`)가 아니다 (4-3).
    """
    return gate.has_location and gate.data.weather


def weather_checked(gate: Gate, weather: WeatherBrief) -> bool:
    """오늘 날씨를 실제로 읽었는가. 부르지 않았거나(`checks_weather` 거짓) 예보 · 특보를 못 읽은
    날은 아니다 — 모델에게 날씨를 말할 거리가 없다. 못 읽었어도 읽은 신호가 나빠 막힌 날이
    있어서, 화면 문구(notices)가 아니라 `unread` 로 본다 (#645 리뷰).
    """
    return checks_weather(gate) and not weather.unread


def reads_affinity(gate: Gate) -> bool:
    """search_activity_memory 가 profile_affinity 를 읽을 월령인가."""
    return gate.stage.months >= AFFINITY_MIN_MONTH
