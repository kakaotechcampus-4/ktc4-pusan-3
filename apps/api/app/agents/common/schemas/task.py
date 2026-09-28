"""Supervisor 가 도메인 Agent 에게 넘기는 봉투.

Supervisor 는 tool 을 모른다. 어느 Agent 의 어느 라벨인지까지만 정하고,
그 라벨에서 무엇을 열지는 각 Agent 의 registry 가 정한다.
"""

from dataclasses import dataclass

# 유형별 묶기는 agent × task_type 당 하나, 상한(2)은 agent 단위로 센다.
MAX_DOMAIN_AGENTS = 2


@dataclass(frozen=True)
class DomainTask:
    run_id: str
    agent: str  # food · activity · growth · health
    task_type: str | None  # Activity 는 라벨이 없어 None
    request_texts: tuple[str, ...]
