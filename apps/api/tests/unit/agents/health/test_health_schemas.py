"""Health 라벨 — 다섯 자리. 비었거나 모르는 값은 짐작하지 않는다."""

import pytest

from app.agents.common.schemas.task import DomainTask
from app.agents.health.schemas.task import HealthTaskType, task_type_of
from tests.unit.agents.health.support import task


def test_라벨은_다섯이다():
    assert {t.value for t in HealthTaskType} == {
        "visit_summary",
        "schedule_check",
        "place_lookup",
        "medication",
        "care_handoff",
    }


def test_라벨을_꺼낸다():
    assert task_type_of(task("medication")) is HealthTaskType.MEDICATION


@pytest.mark.parametrize("label", [None, "growth_review", ""])
def test_비었거나_모르는_라벨은_None(label):
    assert task_type_of(task(label)) is None


def test_Health_가_아닌_task_는_거절():
    other = DomainTask(run_id="r", agent="growth", task_type="medication", request_texts=("x",))
    with pytest.raises(ValueError):
        task_type_of(other)
