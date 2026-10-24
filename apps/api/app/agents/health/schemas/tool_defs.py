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
from app.agents.health.schemas.records import ComposeTimelineArgs, SearchHealthArgs

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_health_observations",
        description=(
            "진료 준비를 정리할 때 먼저 한 번 부른다. 보호자가 말한 기간(없으면 14d)과, 특정 "
            "증상만 원하면 그 증상 말을 넣는다. 그 범위의 증상 · 먹인 약 기록을 정리에 담고 "
            "줄 수만 돌려준다."
        ),
        args=SearchHealthArgs,
    ),
    ToolDefinition(
        name="compose_symptom_timeline",
        description=(
            "진료 준비의 마지막에 한 번 부른다. 인자가 없다. 조회한 증상 · 먹인 약과 "
            "최근 3일 식사 · 복약 체크 · 알레르기 · 만성질환을 코드가 날짜순으로 정리한다."
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
