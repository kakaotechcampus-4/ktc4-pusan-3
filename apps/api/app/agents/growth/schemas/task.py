"""Supervisor -> Growth 로 오는 task.

봉투는 공통 `DomainTask` 다. routing 은 Supervisor 라벨을 문자열 그대로 넘기고, enum 으로 바꾸는
것은 여기서 한다. Growth 는 Food 처럼 라벨이 항상 있다 — 라벨마다 열리는 tool 이 다르다.
"""

from enum import StrEnum

from app.agents.common.schemas.task import DomainTask


class GrowthTaskType(StrEnum):
    LEARNING_SUGGESTION = "learning_suggestion"  # 교육 활동
    ROUTINE_COACHING = "routine_coaching"  # 생활 루틴
    BOOK_SUGGESTION = "book_suggestion"  # 도서
    GROWTH_REVIEW = "growth_review"  # 성장 추이 — 모델 0회


def task_type_of(task: DomainTask) -> GrowthTaskType:
    """봉투에서 Growth 의 task 유형을 꺼낸다."""
    if task.agent != "growth":
        raise ValueError(f"Growth 가 아닌 task 를 받았다: agent={task.agent}")
    if task.task_type is None:
        raise ValueError("Growth task 에 task_type 이 없다")
    return GrowthTaskType(task.task_type)
