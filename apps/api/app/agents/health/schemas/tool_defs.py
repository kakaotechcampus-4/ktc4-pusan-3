"""모델에게 보이는 Health tool 의 이름 · 호출 조건 · argument 모델.

`registry.MODEL_TOOLS` 는 명세 §1 전체를 이미 담고 있고, 열리는 것은 정의와 핸들러가 둘 다 있는
tool 뿐이다. tool 을 만드는 이슈가 정의와 핸들러를 같이 더한다. description 에는 '무엇을 하는가'보다
'언제 부르는가'를 적는다.
"""

from app.agents.common.tool_schema import ToolDefinition
from app.agents.health.schemas.medication import (
    CreateMedicationArgs,
    DeferDoseQuestionArgs,
    SearchMedicationArgs,
    ShowDoseStatusArgs,
    StopMedicationArgs,
    UpdateMedicationArgs,
)
from app.agents.health.schemas.records import (
    ComposeTimelineArgs,
    SearchHealthArgs,
    ShowFeverTimelineArgs,
)

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
        name="show_fever_timeline",
        description=(
            "열이 언제부터였는지 · 몇 도였는지만 물을 때 정리 대신 한 번 부른다. 인자가 없다. "
            "최근 48시간 체온 · 체크된 복약 · 먹인 약을 코드가 시간순으로 낸다. '요즘' · '최근' · "
            "'지난주' 처럼 며칠 넘는 기간을 물었으면 부르지 않고 정리한다. "
            "다른 출력 tool 과 같은 응답에서 부르지 않는다 — 정리까지 요청했으면 "
            "compose_symptom_timeline 만 부른다."
        ),
        args=ShowFeverTimelineArgs,
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
    ToolDefinition(
        name="show_dose_status",
        description=(
            "등록된 약을 오늘 몇 번 체크했는지 · 마지막으로 언제 먹였는지 · 다음은 언제인지 물을 "
            "때 마지막에 한 번 부른다. 인자가 없다. 체크된 회차 사실만 코드가 낸다. "
            "다른 출력 tool 과 같은 응답에서 부르지 않는다 — 새 약 · 수정 · 중단 요청이 "
            "같이 있으면 그 tool 만 부른다."
        ),
        args=ShowDoseStatusArgs,
    ),
    ToolDefinition(
        name="defer_dose_question",
        description=(
            "용량 · 간격을 바꿔도 되는지(늘려도 · 줄여도) 묻기만 하고 바꿀 값을 말하지 않았을 "
            "때 마지막에 한 번 부른다. 인자가 없다. 코드가 처방한 곳에 확인하라는 안내를 낸다. "
            "다른 출력 tool 과 같은 응답에서 부르지 않는다 — 새 약 · 수정 · 중단 요청이 "
            "같이 있으면 그 tool 만 부른다."
        ),
        args=DeferDoseQuestionArgs,
    ),
]
