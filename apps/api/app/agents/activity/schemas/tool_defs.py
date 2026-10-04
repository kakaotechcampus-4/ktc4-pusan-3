"""모델에게 보이는 Activity tool 5개의 이름 · 호출 조건 · argument 모델.

description 에는 '무엇을 하는가'보다 '언제 부르는가'를 적는다.
안전 필터 · 중복 제거 · 문서 행 조회는 코드 tool 이라 여기 없다 (registry.CODE_TOOLS).
놀이 후기 기록은 쓰기라 Memory 가 한다.
"""

from app.agents.activity.schemas.memory import SearchActivityMemoryArgs
from app.agents.activity.schemas.outing import (
    LookupScheduleArgs,
    LookupWeatherArgs,
    SearchNearbyPlacesArgs,
)
from app.agents.activity.schemas.recommend import ProposeActivityCandidatesArgs
from app.agents.common.tool_schema import ToolDefinition

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_activity_memory",
        description=(
            "아이가 최근 한 놀이와 놀이 관심(좋아함·싫어함)을 근거 순서대로 찾는다. "
            "활동을 고르기 전에 먼저 부른다. 결과의 id 만 evidence 로 쓸 수 있다."
        ),
        args=SearchActivityMemoryArgs,
    ),
    ToolDefinition(
        name="lookup_weather",
        description=(
            "날씨 판정(야외 가능 여부)과 등급 라벨을 가져온다. "
            "why_now 에 날씨를 쓸 때 부른다. 수치는 오지 않는다 — 라벨만 쓴다."
        ),
        args=LookupWeatherArgs,
    ),
    ToolDefinition(
        name="lookup_schedule",
        description=(
            "아이 일정과 비는 시간을 가져온다. "
            "일정과 겹치지 않게 하거나 언제 하면 좋을지 쓸 때 부른다. 읽기만 한다."
        ),
        args=LookupScheduleArgs,
    ),
    ToolDefinition(
        name="search_nearby_places",
        description=(
            "근처 공원·놀이터·도서관 등을 종류로 찾는다. 나들이 후보를 낼 때 부른다. "
            "바깥 활동이 어려운 날은 library · indoor_playground · experience_center 만 찾는다. "
            "place_name 에는 이 결과에 있는 이름만 쓴다."
        ),
        args=SearchNearbyPlacesArgs,
    ),
    ToolDefinition(
        name="propose_activity_candidates",
        description=(
            "놀이 후보 3개를 제출한다. 마지막에 한 번 부른다. "
            "쓰는 물건은 materials 에 빠짐없이 적는다 — 안전 검사는 코드가 한다."
        ),
        args=ProposeActivityCandidatesArgs,
    ),
]
