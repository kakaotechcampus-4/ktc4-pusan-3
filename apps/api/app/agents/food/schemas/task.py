"""Supervisor -> Food로 오는 task.

봉투는 공통 `DomainTask`다. routing 은 Supervisor 라벨을 문자열 그대로 넘기고,
enum 으로 바꾸는 것은 여기서 한다.
"""

from app.agents.common.schemas.task import DomainTask
from app.agents.food.schemas.common import FoodTaskType


def task_type_of(task: DomainTask) -> FoodTaskType:
    """봉투에서 Food의 task 유형을 꺼낸다. Food 는 라벨이 항상 있다 (Supervisor 스키마가 강제)."""
    if task.agent != "food":
        raise ValueError(f"Food 가 아닌 task 를 받았다: agent={task.agent}")
    if task.task_type is None:
        raise ValueError("Food task 에 task_type 이 없다")
    return FoodTaskType(task.task_type)
