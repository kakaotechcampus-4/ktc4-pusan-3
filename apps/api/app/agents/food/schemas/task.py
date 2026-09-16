"""Supervisor -> Food로 오는 task"""

from dataclasses import dataclass

from app.agents.food.schemas.common import FoodTaskType


@dataclass(frozen=True)
class FoodTask:
    run_id: str
    task_type: FoodTaskType
    request_texts: tuple[str, ...]  # food로 온 REQUEST 조각 원문
