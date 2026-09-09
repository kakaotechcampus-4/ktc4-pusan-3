"""tool 27개의 이름 · 호출 조건 · argument 모델 정의.

언제 부르고 언제 부르지 않는지 tool의 경계를 적는다.
"""

from app.agents.memory.schemas.observation import (
    ObservationActivityCreate,
    ObservationActivityUpdate,
    ObservationEducationCreate,
    ObservationEducationUpdate,
    ObservationFoodCreate,
    ObservationFoodUpdate,
    ObservationHealthCreate,
    ObservationHealthUpdate,
    ObservationQueryArgs,
    RecordRef,
)
from app.agents.memory.schemas.parse_input import ParseInputArgs
from app.agents.memory.schemas.schedule import (
    EventCreate,
    EventItemCreate,
    EventItemRef,
    EventItemUpdate,
    EventQuery,
    EventRef,
    EventUpdate,
    ReminderCreate,
    ReminderRef,
    ReminderUpdate,
)
from app.agents.memory.schemas.tool_schema import ToolDefinition

_NEEDS_QUERY = ("대상 id 를 모르면 먼저 조회 tool을 부른다. id를 지어내지 않는다.")

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="parse_input",
        description=(
            "서로 독립된 정보나 요청이 2개 이상 섞인 입력을 의미 단위로 나눈다. "
            "그럴 때만 다른 tool 보다 먼저 한 번 부른다. 정보가 하나뿐이면 부르지 않고 "
            "바로 해당 tool 을 부른다. 저장이나 조회는 하지 않는다."
        ),
        args=ParseInputArgs,
    ),
    # observation_food
    ToolDefinition(
        name="create_observation_food",
        description=(
            "아이가 실제로 먹거나 마신 것, 음식에 보인 반응을 기록한다. "
            "앞으로 먹일 계획이나 메뉴 추천 요청에는 부르지 않는다."
        ),
        args=ObservationFoodCreate,
    ),
    ToolDefinition(
        name="query_observation_food",
        description=f"저장된 음식 기록을 날짜나 키워드로 찾는다. {_NEEDS_QUERY}",
        args=ObservationQueryArgs,
    ),
    ToolDefinition(
        name="update_observation_food",
        description=f"이미 저장된 음식 기록의 내용을 고친다. {_NEEDS_QUERY}",
        args=ObservationFoodUpdate,
    ),
    ToolDefinition(
        name="delete_observation_food",
        description=f"음식 기록 한 건을 지운다. {_NEEDS_QUERY}",
        args=RecordRef,
    ),
    # observation_health
    ToolDefinition(
        name="create_observation_health",
        description=(
            "관찰된 증상이나 컨디션을 기록한다. 발화에 드러난 것만 담고 원인을 추정하지 않는다. "
            "알레르기·만성질환 등록에는 쓰지 않는다 — 그건 보호자가 직접 입력한다."
        ),
        args=ObservationHealthCreate,
    ),
    ToolDefinition(
        name="query_observation_health",
        description=f"저장된 증상 기록을 날짜나 키워드로 찾는다. {_NEEDS_QUERY}",
        args=ObservationQueryArgs,
    ),
    ToolDefinition(
        name="update_observation_health",
        description=f"이미 저장된 증상 기록을 고친다. {_NEEDS_QUERY}",
        args=ObservationHealthUpdate,
    ),
    ToolDefinition(
        name="delete_observation_health",
        description=f"증상 기록 한 건을 지운다. {_NEEDS_QUERY}",
        args=RecordRef,
    ),
    # observation_education
    ToolDefinition(
        name="create_observation_education",
        description=(
            "학습 주제가 분명한 활동을 기록한다. 책읽기·활동지·한글/숫자 학습처럼 "
            "topic 을 말할 수 있으면 이쪽이다. 그냥 논 것은 activity 로 보낸다."
        ),
        args=ObservationEducationCreate,
    ),
    ToolDefinition(
        name="query_observation_education",
        description=f"저장된 학습 기록을 날짜나 키워드로 찾는다. {_NEEDS_QUERY}",
        args=ObservationQueryArgs,
    ),
    ToolDefinition(
        name="update_observation_education",
        description=f"이미 저장된 학습 기록을 고친다. {_NEEDS_QUERY}",
        args=ObservationEducationUpdate,
    ),
    ToolDefinition(
        name="delete_observation_education",
        description=f"학습 기록 한 건을 지운다. {_NEEDS_QUERY}",
        args=RecordRef,
    ),
    # observation_activity
    ToolDefinition(
        name="create_observation_activity",
        description=(
            "놀이·자유활동·신체활동을 기록한다. 블록놀이, 바깥놀이처럼 학습 목표가 없는 활동이다. "
            "학습 topic이 분명하면 education을 쓴다."
        ),
        args=ObservationActivityCreate,
    ),
    ToolDefinition(
        name="query_observation_activity",
        description=f"저장된 활동 기록을 날짜나 키워드로 찾는다. {_NEEDS_QUERY}",
        args=ObservationQueryArgs,
    ),
    ToolDefinition(
        name="update_observation_activity",
        description=f"이미 저장된 활동 기록을 고친다. {_NEEDS_QUERY}",
        args=ObservationActivityUpdate,
    ),
    ToolDefinition(
        name="delete_observation_activity",
        description=f"활동 기록 한 건을 지운다. {_NEEDS_QUERY}",
        args=RecordRef,
    ),
    # event
    ToolDefinition(
        name="create_event",
        description=(
            "앞으로의 일정을 등록한다. 운동회·병원·참관수업처럼 날짜가 있는 예정에 쓴다. "
            "이미 지난 일을 기록하는 건 observation이다."
        ),
        args=EventCreate,
    ),
    ToolDefinition(
        name="query_event",
        description=(
            "일정을 날짜 범위나 이름으로 찾는다. 준비물과 알림도 함께 돌려준다. "
            "일정 수정·삭제, 알림 추가 전에 id 를 얻으려면 먼저 이 tool 을 부른다."
        ),
        args=EventQuery,
    ),
    ToolDefinition(
        name="update_event",
        description=f"이미 등록된 일정의 정보를 고친다. {_NEEDS_QUERY}",
        args=EventUpdate,
    ),
    ToolDefinition(
        name="delete_event",
        description=f"일정 한 건을 지운다. 연결된 준비물과 알림도 함께 삭제한다. {_NEEDS_QUERY}",
        args=EventRef,
    ),
    # event_item
    ToolDefinition(
        name="create_event_item",
        description=(
            "일정에 챙길 준비물을 하나 추가한다. 준비물이 여러 개면 하나씩 나눠서 부른다. "
            "event_id 는 create_event 나 query_event 결과에서 가져온다."
        ),
        args=EventItemCreate,
    ),
    ToolDefinition(
        name="update_event_item",
        description=f"준비물 이름을 고치거나 챙김 여부를 표시한다. {_NEEDS_QUERY}",
        args=EventItemUpdate,
    ),
    ToolDefinition(
        name="delete_event_item",
        description=f"준비물 한 개를 목록에서 뺀다. {_NEEDS_QUERY}",
        args=EventItemRef,
    ),
    # reminder
    ToolDefinition(
        name="create_reminder",
        description=(
            "일정에 알림을 건다. '전날 저녁 8시'처럼 일정 기준 상대 표현이면 "
            "remind_on 을 비우고 offset_days_from_event 를 쓴다. "
            "event_id 는 create_event 나 query_event 결과에서 가져온다."
        ),
        args=ReminderCreate,
    ),
    ToolDefinition(
        name="update_reminder",
        description=f"예약된 알림 정보를 수정한다. {_NEEDS_QUERY}",
        args=ReminderUpdate,
    ),
    ToolDefinition(
        name="delete_reminder",
        description=f"알림 한 건을 해제한다. {_NEEDS_QUERY}",
        args=ReminderRef,
    ),
]
