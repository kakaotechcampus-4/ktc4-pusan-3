"""모델에게 보이는 Health tool 의 이름 · 호출 조건 · argument 모델.

`registry.MODEL_TOOLS` 는 명세 §1 전체를 이미 담고 있고, 열리는 것은 정의와 핸들러가 둘 다 있는
tool 뿐이다. tool 을 만드는 이슈가 정의와 핸들러를 같이 더한다. description 에는 '무엇을 하는가'보다
'언제 부르는가'를 적는다.
"""

from app.agents.common.tool_schema import ToolDefinition
from app.agents.health.schemas.medication import (
    CreateMedicationArgs,
    SearchMedicationArgs,
    StopMedicationArgs,
    UpdateMedicationArgs,
)
from app.agents.health.schemas.records import (
    ComposeTimelineArgs,
    SearchHealthArgs,
    SearchMealsArgs,
    SearchMedicationChecksArgs,
)

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_health_observations",
        description=(
            "진료 준비를 정리할 때 가장 먼저 부른다. 최근 증상 기록을 에피소드(같은 증상이 이어진 "
            "기간)로 묶은 줄과 보호자가 기록한 먹인 약 줄을 준다. 줄마다 key · 날짜와, 증상 줄은 "
            "증상 코드(사전에 없는 증상이면 빈 목록)가 있다. 특정 증상만 원하면 symptom 에 그 말을 "
            "넣는다."
        ),
        args=SearchHealthArgs,
    ),
    ToolDefinition(
        name="search_recent_meals",
        description=(
            "진료 준비에 최근 3일 식사를 곁들일 때 부른다. 식사 한 건마다 key · 날짜를 준다."
        ),
        args=SearchMealsArgs,
    ),
    ToolDefinition(
        name="search_medication_checks",
        description=(
            "진료 준비에 복약 이력을 넣을 때 부른다. 끝났거나 멈춘 약도 기간이 겹치면 포함해 "
            "날짜 · 약별 체크 줄의 key · 날짜를 준다."
        ),
        args=SearchMedicationChecksArgs,
    ),
    ToolDefinition(
        name="compose_symptom_timeline",
        description=(
            "진료 준비의 마지막에 한 번 부른다. 정리에 넣을 줄의 key 를 조회 결과에서 그대로 "
            "복사해 넘긴다. 줄의 글 · 날짜순 정렬 · 알레르기 · 만성질환 줄은 코드가 만든다."
        ),
        args=ComposeTimelineArgs,
    ),
    ToolDefinition(
        name="search_medication_schedules",
        description=(
            "지금 진행 중인 복약(약 이름 · 용량 · 알림 시각 · 기간)을 가져온다. 새 약을 등록하거나 "
            "바꾸기 전에 이미 등록된 약이 있는지 볼 때 부른다."
        ),
        args=SearchMedicationArgs,
    ),
    ToolDefinition(
        name="create_medication_schedule",
        description=(
            "새로 먹이기 시작하는 약의 복약 알림 초안을 낸다. 보호자가 새 약과 먹이는 방법을 "
            "말했을 때 마지막에 한 번 부른다. 값은 전부 요청 원문에서 복사한 말이다. 저장하지 "
            "않는다 — 보호자가 확인 카드에서 제출해야 등록된다."
        ),
        args=CreateMedicationArgs,
    ),
    ToolDefinition(
        name="update_medication_schedule",
        description=(
            "이미 등록된 약의 시각 · 양 · 기간을 바꾸는 초안을 낸다. 보호자가 등록된 약을 바꿔 "
            "달라고 했을 때 마지막에 한 번 부른다. 바꾸지 않은 칸은 비운다. 저장하지 않는다."
        ),
        args=UpdateMedicationArgs,
    ),
    ToolDefinition(
        name="stop_medication_schedule",
        description=(
            "복약 알림을 멈추려는 말이 나왔을 때 마지막에 한 번 부른다(약 끊었어 · 그만 먹여도 "
            "돼). 바로 멈추지 않고 보호자에게 한 번 확인한다. 약 이름을 말하지 않았으면 비운다."
        ),
        args=StopMedicationArgs,
    ),
]
