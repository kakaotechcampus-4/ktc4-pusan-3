"""tool 28개의 이름 · 호출 조건 · argument 모델 정의.

언제 부르고 언제 부르지 않는지 tool의 경계를 적는다.
"""

from app.agents.common.tool_schema import ToolDefinition
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
    ObservationRoutineCreate,
    ObservationRoutineUpdate,
    RecordRef,
)
from app.agents.memory.schemas.schedule import (
    EventCreate,
    EventItemCreate,
    EventItemRef,
    EventItemUpdate,
    EventQuery,
    EventRef,
    EventUpdate,
)

_NEEDS_QUERY = "대상 id 를 모르면 먼저 조회 tool을 부른다. id를 지어내지 않는다."

TOOL_DEFINITIONS: list[ToolDefinition] = [
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
    # observation_routine
    ToolDefinition(
        name="create_observation_routine",
        description=(
            "아이가 반복하는 생활 행동, 스스로 해낸 일, 생활 습관, 인사 같은 사회적 생활기술을 "
            "기록한다. 양치·옷 입기·정리·인사·손톱 물어뜯기·등원 준비. "
            "놀이 자체는 activity, 먹은 음식은 food, 증상은 health로 보낸다."
        ),
        args=ObservationRoutineCreate,
    ),
    ToolDefinition(
        name="query_observation_routine",
        description=f"저장된 생활 행동 기록을 날짜나 키워드로 찾는다. {_NEEDS_QUERY}",
        args=ObservationQueryArgs,
    ),
    ToolDefinition(
        name="update_observation_routine",
        description=f"이미 저장된 생활 행동 기록을 고친다. {_NEEDS_QUERY}",
        args=ObservationRoutineUpdate,
    ),
    ToolDefinition(
        name="delete_observation_routine",
        description=f"생활 행동 기록 한 건을 지운다. {_NEEDS_QUERY}",
        args=RecordRef,
    ),
    # event
    ToolDefinition(
        name="create_event",
        description=(
            "앞으로의 일정을 초안으로 만든다. 운동회·병원·참관수업처럼 날짜가 있는 예정에 쓴다. "
            "챙길 준비물이 같이 나오면 items 에 모두 넣는다. 이 tool 한 번으로 끝내고 "
            "준비물을 따로 부르지 않는다. "
            "이미 지난 일을 기록하는 건 observation이다."
        ),
        args=EventCreate,
    ),
    ToolDefinition(
        name="query_event",
        description=(
            "일정을 날짜 범위나 이름으로 찾는다. 준비물도 함께 돌려준다. "
            "일정 수정·삭제, 준비물 추가 전에 id 를 얻으려면 먼저 이 tool 을 부른다."
        ),
        args=EventQuery,
    ),
    ToolDefinition(
        name="update_event",
        description=(
            "이미 저장된 일정을 고친 초안을 만든다. 보호자가 확인해야 반영된다. "
            f'끝나는 시각을 없애려면 clear=["ends_at"]을 쓴다. {_NEEDS_QUERY}'
        ),
        args=EventUpdate,
    ),
    ToolDefinition(
        name="delete_event",
        description=f"일정 한 건을 지운다. 연결된 준비물도 함께 삭제한다. {_NEEDS_QUERY}",
        args=EventRef,
    ),
    # event_item
    ToolDefinition(
        name="create_event_item",
        description=(
            "이미 있는 일정에 챙길 준비물을 하나 추가한다. "
            "새 일정이면 create_event의 items를 쓴다. "
            f"준비물이 여러 개면 준비물마다 따로 부르되, 한 응답에 모두 부른다. {_NEEDS_QUERY}"
        ),
        args=EventItemCreate,
    ),
    ToolDefinition(
        name="update_event_item",
        description=(
            "준비물 이름을 고치거나 챙김 여부를 표시한다. "
            f"이름을 고치면 보호자 확인을 거치고, 챙김 표시는 바로 반영된다. {_NEEDS_QUERY}"
        ),
        args=EventItemUpdate,
    ),
    ToolDefinition(
        name="delete_event_item",
        description=f"준비물 한 개를 목록에서 뺀다. {_NEEDS_QUERY}",
        args=EventItemRef,
    ),
]
