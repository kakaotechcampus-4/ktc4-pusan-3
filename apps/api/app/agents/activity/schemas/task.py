"""Supervisor -> Activity로 오는 task.

봉투는 공통 `DomainTask`다. Supervisor는 Activity에 라벨을 주지 않는다 —
분기 축이 task 유형이 아니라 월령이고, 월령은 코드가 계산해서 모델이 고를 것이 없다 (D1).
"""

from enum import StrEnum

from app.agents.common.schemas.task import DomainTask


class ActivityTaskType(StrEnum):
    ACTIVITY_RECOMMENDATION = "activity_recommendation"


def task_type_of(task: DomainTask) -> ActivityTaskType:
    """봉투에서 Activity의 task 유형을 꺼낸다. 라벨이 없으면(None) 놀이 추천 하나다."""
    if task.agent != "activity":
        raise ValueError(f"Activity 가 아닌 task 를 받았다: agent={task.agent}")
    if task.task_type is None:
        return ActivityTaskType.ACTIVITY_RECOMMENDATION
    return ActivityTaskType(task.task_type)
