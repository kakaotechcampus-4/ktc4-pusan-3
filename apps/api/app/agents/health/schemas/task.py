"""Supervisor -> Health 로 오는 task.

봉투는 공통 `DomainTask` 다. routing 은 Supervisor 라벨을 문자열 그대로 넘기고, enum 으로 바꾸는
것은 여기서 한다. Growth 와 달리 라벨이 비거나 모르는 값이어도 예외를 내지 않는다 — 짐작해서
다른 라벨로 보내지 않고 `None` 을 돌려주면 `run()` 이 `unsupported` 로 닫는다.
"""

from enum import StrEnum

from app.agents.common.schemas.task import DomainTask


class HealthTaskType(StrEnum):
    VISIT_SUMMARY = "visit_summary"  # 진료 준비 정리 · 열 타임라인
    SCHEDULE_CHECK = "schedule_check"  # 검진 · 접종 안내
    PLACE_LOOKUP = "place_lookup"  # 병원 찾기
    MEDICATION = "medication"  # 복약 기록 · 초안
    CARE_HANDOFF = "care_handoff"  # 투약의뢰서 · 응급 카드


def task_type_of(task: DomainTask) -> HealthTaskType | None:
    """봉투에서 Health 의 task 유형을 꺼낸다. 비었거나 모르는 라벨은 None — 짐작하지 않는다."""
    if task.agent != "health":
        raise ValueError(f"Health 가 아닌 task 를 받았다: agent={task.agent}")
    try:
        return HealthTaskType(task.task_type)
    except ValueError:
        return None
