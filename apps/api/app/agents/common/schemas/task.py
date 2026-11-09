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
    # pipeline 이 이 task 를 돌리는 run 의 마감 시각(`time.perf_counter` 기준). 없으면 마감이 없다.
    # Growth 는 재호출이 마감에 걸리면 처음 통과한 후보를 지키려고 이 값을 본다
    deadline: float | None = None
    # 되물은 질문에 보호자가 답한 뒤 같은 요청을 이어서 도는 task 다 (pipeline 의 이어받기).
    # 답을 받은 행동의 저장 이름이다. 그 행동은 수준을 못 읽어도 다시 묻지 않는다
    answered_subject: str | None = None
    # 그 답의 원문. 코드 판정(닫힘 · 요청 행동)만 읽고 모델에게는 보내지 않는다 — Memory 가 저장하지
    # 않은 말까지 외부 모델로 가지 않게 한다. Growth 는 저장된 기록을 조회로 읽는다
    answer_texts: tuple[str, ...] = ()
    # 이번 입력에서 보호자가 실제로 한 말(원문 조각). 앞 대화를 이어 묻는 말을 Supervisor 가
    # 해석해 `request_texts` 를 모델이 쓴 문장으로 바꿨을 때만 찬다 — 비어 있으면
    # `request_texts` 가 곧 원문이다(`originals()`). "보호자가 말했는가" 를 코드가 확인하는
    # 곳(지역 이름 · 요청한 놀이 · 근거 수치 · 도서 검색어)과 되묻기 맥락의 hint 는 원문을
    # 읽는다 — 해석한 문장에 든 앞 추천
    # 이름(AI 가 쓴 글)이 보호자 말로 통과하거나 기록 경로로 가지 않게. 닫힘 판정은 원문과 해석을
    # 둘 다 본다. 모델에게 보내는 요청 · 주제는 `request_texts` 를 읽는다
    original_texts: tuple[str, ...] = ()
    # 앞 대화를 이어 묻는 task 만 — 앞 턴들에서 보호자가 입력한 원문(오래된 순). 앞에서 말한 지역 ·
    # 놀이를 이어 묻는 말("그럼 다른 데는?")이 "보호자가 말하지 않았다" 로 막히지 않게 Activity 의
    # 출처 확인만 읽는다. Memory · 되묻기 맥락으로는 가지 않는다
    prior_texts: tuple[str, ...] = ()
    # 앞 대화를 읽은 task 만 — 앞 턴들에서 같은 Agent 가 낸 추천 이름(오래된 순, 겹침 없음).
    # "다른 거 추천해줘" 를 여러 번 물어도 이미 보여 준 것을 다시 내지 않게 Agent 가 읽는다
    # (#844). AI 가 쓴 이름이라 "보호자가 말했는가" 확인(`originals`)에는 쓰지 않는다
    shown_before: tuple[str, ...] = ()

    def originals(self) -> tuple[str, ...]:
        return self.original_texts or self.request_texts
