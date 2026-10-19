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
    # 같은 입력의 기록 조각(Memory 몫). 모델에게 보내지 않는다 —
    # 코드 판정만 읽는다(Growth 루틴 닫힘).
    # "약 먹기 싫어해. 어떻게 하지?" 는 기록과 요청으로 갈려 요청 조각만으로는 무엇을 묻는지 모른다.
    # 05 다시 시도(entrypoint.suggest)는 싣지 않는다 —
    # 닫힌 run 은 실패가 아니라 다시 시도 맥락이 없다
    record_texts: tuple[str, ...] = ()
