"""Food 가 공통 봉투(DomainTask)에서 자기 유형을 꺼내는지. routing 은 라벨을 문자열로만 넘긴다."""

import pytest

from app.agents.common.schemas.task import DomainTask
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.task import task_type_of


def _task(agent: str = "food", task_type: str | None = "meal_recommendation") -> DomainTask:
    return DomainTask(
        run_id="run-1", agent=agent, task_type=task_type, request_texts=("저녁 뭐 먹일까",)
    )


def test_라벨을_enum_으로_바꾼다() -> None:
    assert task_type_of(_task()) is FoodTaskType.MEAL_RECOMMENDATION


def test_다른_agent_의_task_는_받지_않는다() -> None:
    with pytest.raises(ValueError):
        task_type_of(_task(agent="activity", task_type=None))


def test_라벨이_없으면_받지_않는다() -> None:
    # Supervisor 스키마가 food 에 라벨을 강제한다. 여기까지 None 이 왔으면 routing 버그다
    with pytest.raises(ValueError):
        task_type_of(_task(task_type=None))


def test_모르는_라벨은_받지_않는다() -> None:
    with pytest.raises(ValueError):
        task_type_of(_task(task_type="snack_idea"))
