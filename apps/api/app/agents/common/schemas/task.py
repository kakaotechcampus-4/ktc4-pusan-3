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
    # 같은 입력의 기록 조각(Memory 몫). Growth 는 모델 메시지에 요청과 따로 싣고(주제를 모른 채
    # 요청 조각만 받지 않게), 그 밖의 Agent 는 모델에게 보내지 않는다 — 코드 판정만 읽는다
    # (Growth 루틴 닫힘).
    # "약 먹기 싫어해. 어떻게 하지?" 는 기록과 요청으로 갈려 요청 조각만으로는 무엇을 묻는지 모른다.
    # 05 다시 시도(entrypoint.suggest)는 싣지 않는다 —
    # 닫힌 run 은 실패가 아니라 다시 시도 맥락이 없다
    record_texts: tuple[str, ...] = ()
    # 같은 run 에서 Memory 가 저장한 루틴 관찰의 행동 이름(저장 순서). 코드 판정만 읽는다 —
    # "밥 먹을 때 힘들어해. 어떻게 하면 좋을까?" 에서 모델이 요청 조각만 보고 다른 행동을 적어도
    # 보호자가 말한 행동을 코드가 안다. 05 다시 시도는 Memory 가 없어 싣지 않는다
    recorded_subjects: tuple[str, ...] = ()
