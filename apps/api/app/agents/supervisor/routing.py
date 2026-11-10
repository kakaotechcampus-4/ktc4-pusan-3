"""Supervisor 출력으로부터 실제 호출 대상을 결정한다.
추가 LLM 호출 없이 코드 규칙만으로 처리한다.

이 모듈에서 결정하는 것:
  - 의도 유형
      record(기록형) / request(요청형) / mixed(혼합형)
      Supervisor 결과를 바탕으로 코드에서 결정하며, LLM에 다시 묻지 않는다.

  - MemoryTask
      원문 전체와 RECORD 조각 힌트, UNCLEAR 조각 원문을 전달한다.
      조각 밖에 부르는 말 · 추임새(`FILLER_WORDS`)만 남은 것은 놓친 기록이 아니다 (#861).
      원문이 전부 요청·안내 조각이면 만들지 않는다 (SKIP_MEMORY_FOR_PURE_REQUEST).
      unclear 와 요청·안내 조각만 있을 때도 만들지 않는다 — 답은 그쪽이 낸다.

  - DomainTask
      구현된 agent 로 간 REQUEST 조각을 agent × task_type 당 하나로 묶는다.
      task_type 은 Supervisor 라벨 문자열 그대로다. enum 변환은 각 Agent 의 task_type_of 가 한다.
      라벨이 없는 Agent(activity)는 None 이다. Health 라벨이 비어 있어도 채우지 않고 None 으로
      넘긴다 — Health 가 unsupported 로 닫는다.
      Growth 추천 라벨은 run 당 하나 — 처음 것이되 늘 닫히는 도서는 다른 추천 라벨에 자리를 내준다.
      앞 대화를 이어 묻는 말이면 추천 task 에 한해 `resolved_request` 가 조각 원문 대신 간다
      (`RESOLVABLE_TASKS`). 그때 보호자 원문은 `original_texts` 에 따로 든다. 확인 · 쓰기 task 와
      Memory 는 늘 보호자 원문을 받는다.

  - 미구현 Agent 처리
      구현된 Agent 가 아닌 곳(`IMPLEMENTED_AGENTS` 밖)으로 라우팅된 조각은
      대상 Agent 정보만 남기고 실제 호출은 하지 않는다.

  - Agent 호출 상한
      도메인 Agent는 최대 2개까지 호출한다.
      상한을 초과하면 뒤쪽 Agent를 제외한다.

  - 안내 문구
      guarded / out_of_scope 조각에는 코드에 정의된 고정 문구를 사용한다.
      diagnosis · out_of_scope 는 같은 code 에 문구가 두 벌이라 조각 글에 맞는 쪽을 고른다 (#864).
      guarded/hazard 가 하나라도 있으면 그 안내만 내고 Memory · 도메인 Agent 를 부르지 않는다.

  - 강등(fallback)
      Supervisor 처리에 실패하면 Memory만 호출한다.
      이때 RECORD 힌트와 도메인 Agent 호출은 사용하지 않는다.

이 모듈에서 결정하지 않는 것:
  - 식이 단계: 아이 나이를 바탕으로 각 pipeline에서 계산
  - Memory가 사용할 테이블
  - 도메인 Agent가 사용할 tool
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.agents.common.schemas.task import MAX_DOMAIN_AGENTS, DomainTask
from app.agents.growth.schemas.task import RECOMMENDING_LABELS as GROWTH_RECOMMENDING
from app.agents.growth.schemas.task import YIELDING_LABELS as GROWTH_YIELDING
from app.agents.memory.schemas.task import MemoryHint, MemoryTask, WorkType
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.schemas import (
    DomainAgentName,
    GuardReason,
    SegmentKind,
    SupervisorOutput,
    align_segment,
    normalize,
)
from app.core import deeplinks

logger = logging.getLogger(__name__)

IntentType = Literal["record", "request", "mixed"]

IMPLEMENTED_AGENTS: frozenset[str] = frozenset(
    {
        DomainAgentName.FOOD.value,
        DomainAgentName.ACTIVITY.value,
        DomainAgentName.GROWTH.value,
        DomainAgentName.HEALTH.value,
    }
)


# 한 run 에 추천 라벨을 하나만 돌리는 Agent. 05 의 다시 시도 맥락 · 0개 기록이 (run, agent) 로
# 묶여서, 같은 Agent 의 추천 task 가 둘이면 서로 덮는다. Food 는 추천 라벨이 하나라 해당이 없다
_ONE_RECOMMENDATION: dict[str, frozenset[str]] = {
    DomainAgentName.GROWTH.value: GROWTH_RECOMMENDING,
}
# _ONE_RECOMMENDATION 의 Agent 가 추천 하나를 고를 때 뒤로 미는 라벨 —
# 다른 추천 라벨이 없을 때만 돈다
_YIELDING: dict[str, frozenset[str]] = {
    DomainAgentName.GROWTH.value: GROWTH_YIELDING,
}

# 앞 대화를 이어 묻는 말의 해석 문장(`resolved_request`)을 받는 task. 추천을 내는 task 표
# (`pipeline.RECOMMENDING_TASKS`)와 같은 값이다 — 레이어가 달라 import 하지 않고 테스트가 맞춘다
RESOLVABLE_TASKS: dict[str, frozenset[str | None]] = {
    DomainAgentName.FOOD.value: frozenset({"meal_recommendation"}),
    DomainAgentName.ACTIVITY.value: frozenset({None}),
    DomainAgentName.GROWTH.value: frozenset(GROWTH_RECOMMENDING),
}

# 혼자서는 아이가 겪은 일이 될 수 없는 낱말 — 조각 밖에 남아도 놓친 기록이 아니다 (#861).
# "아니" 는 정정("김밥 아니 볶음밥")에 쓰여 뺀다. "근데" 도 뒤에 기록이 이어지는 연결어라 뺀다
FILLER_WORDS: frozenset[str] = frozenset(
    "야 얘 아 어 음 흠 헐 하 휴 에휴 아이고 저기 저기요 있잖아 제발 진짜 좀 빨리 그니까".split()
)
# ㅠㅠ · ㅋㅋ · ㄱㄱ · ㅇㅇ 처럼 자모만 된 낱말. isalnum() 이 True 라
# 걸러 주지 않으면 안 덮인 글자로 센다
_JAMO_ONLY = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]+")

# True: 순수 요청형(원문이 전부 요청, 안내 조각)이면 Memory를 건너뛴다.
#       (Supervisor가 의도를 제대로 나눴다고 보는 정책으로, 잘못 나누면 입력 정보가 그대로 삭제)
# False: Supervisor가 요청으로만 잘라도 Memory가 원문을 한 번 더 확인
SKIP_MEMORY_FOR_PURE_REQUEST = True


@dataclass(frozen=True)
class Guidance:
    """정해진 안내 한 건.

    계약서의 guards 와 code · message · deeplink 를 공유하지만 같은 것이 아니다.
    guards 는 POST /suggestions 응답 안의 칸이고 blocked_agents 를 더 싣는다.
    이쪽은 입력 run 의 SSE 로 나간다.
    """

    code: str
    message: str
    deeplink: str | None = None


# 위험 물질을 먹거나 마셨다는 입력 (#626). 규칙 사전검사(pipeline)와 Supervisor 의 hazard 라벨이
# 같은 안내를 낸다. 갈 곳이 없는 안내라 deeplink 가 없다.
# 규칙은 "아이가 그랬다면" 으로 시작.
# 삼킨 것의 증상은 늦게 나올 수 있어 "걱정되면" 같은 기다릴 여지를 두지 않고 119 하나만
HAZARD_GUIDANCE = Guidance(
    code=GuardReason.HAZARD.value,
    message=(
        "아이가 먹으면 안 되는 것을 먹거나 마셨다면, 지금 괜찮아 보여도 바로 119에 전화해 주세요. "
        "응급처치와 가까운 병원을 안내받을 수 있어요. "
        "이 내용은 식사·취향 기록으로 남기지 않았어요."
    ),
)

# guard · out_of_scope 별 문구. 서버가 입력 run 으로 내는 딥링크는 여기가 정본이고,
# 값은 core/deeplinks.py 의 웹 경로만 쓴다 (#625)
_GUIDANCE: dict[str, Guidance] = {
    GuardReason.SAFETY_RECORD.value: Guidance(
        code=GuardReason.SAFETY_RECORD.value,
        message="알레르기·건강 정보는 직접 입력해 주세요. 대신 등록해 드릴 수 없어요.",
        # 알레르기 · 건강 정보를 넣는 곳은 10 설정이 아니라 11 아이 프로필이다
        deeplink=deeplinks.PROFILE,
    ),
    # 채팅의 키 · 몸무게는 child_growth_log 에 쓰지 않는다 — 잰 날짜와 단위를 화면에서 받는다.
    # 값이 이상해도("1톤") 같은 안내다. 평가 · 또래 비교 표현은 넣지 않는다 (루트 §2)
    GuardReason.GROWTH_RECORD.value: Guidance(
        code=GuardReason.GROWTH_RECORD.value,
        message=(
            "키·몸무게는 성장 기록에서 잰 날짜와 함께 직접 입력해 주세요. "
            "채팅으로는 저장하지 않아요."
        ),
        deeplink=deeplinks.PROFILE_GROWTH,
    ),
    # 기록을 남겼는지는 적지 않는다 — 묻기만 한 말("해열제 몇 ml 먹여?")은 남길 기록이 없다.
    # 함께 온 증상을 저장했으면 Memory가 따로 알린다.
    # 진단 문의와 약 문의가 한 라벨이라 어디서 확인하면 되는지를 약 얘기가 있을 때만 둘 다 적는다
    # (#633 리뷰 · #864). 약 얘기가 없으면 `PLAIN_DIAGNOSIS_GUIDANCE`
    GuardReason.DIAGNOSIS.value: Guidance(
        code=GuardReason.DIAGNOSIS.value,
        message=(
            "진단이나 결핍 판정, 약·영양제·치료식 추천은 도와드릴 수 없어요. "
            "약 용량은 약 설명서나 약사에게, 아이 상태는 병원에서 확인해 주세요."
        ),
    ),
    SegmentKind.OUT_OF_SCOPE.value: Guidance(
        code=SegmentKind.OUT_OF_SCOPE.value,
        message="구매·예약·연락처럼 대신 해 드려야 하는 일은 도와드릴 수 없어요.",
    ),
    GuardReason.HAZARD.value: HAZARD_GUIDANCE,
}

# Supervisor 가 놓친 알레르기 · 만성질환 언급에 pipeline 이 같은 안내를 낸다 (#639)
SAFETY_GUIDANCE = _GUIDANCE[GuardReason.SAFETY_RECORD.value]

# 같은 code 의 문구 두 벌 — 부모가 묻지 않은 약 · 대행 얘기를 하지 않는다. code 가 같아서 화면
# 계약은 그대로다. 어느 쪽을 낼지는 `_guidance` 가 조각 글을 보고 코드로 고른다 (#864).
# 진단 문의에 약 얘기가 없을 때. 병명 · 판정을 짐작하지 않는다 (루트 §2)
PLAIN_DIAGNOSIS_GUIDANCE = Guidance(
    code=GuardReason.DIAGNOSIS.value,
    message="진단이나 결핍 판정은 도와드릴 수 없어요. 아이 상태는 병원에서 확인해 주세요.",
)
# 다른 집 아이 · 다른 보호자의 기록을 묻는 말. 대행을 시킨 것이 아니라 우리 아이 기록 밖이다
OTHER_FAMILY_GUIDANCE = Guidance(
    code=SegmentKind.OUT_OF_SCOPE.value,
    message="다른 집 아이나 다른 보호자의 기록은 볼 수 없어요. 아이캐치는 우리 아이 기록만 다뤄요.",
)

# 약 얘기 판정 낱말 — 문구를 고르는 데만 쓴다. 놓치면 약 문장이 빠질 뿐이고, 잘못 걸려도 지금과
# 같다. "약" 은 글자로 찾지 않는다(예약 · 약속 · 약간 · 치약)
_TREATMENT_STEMS: tuple[str, ...] = (
    "해열제 해열약 감기약 물약 가루약 시럽 영양제 항생제 소화제 진통제 철분제 유산균 비타민 "
    "타이레놀 부루펜 챔프 맥시부펜 치료식 처방 복용 용량"
).split()
_BARE_MEDICINE = re.compile(r"약(을|은|이|도|만|좀|이랑|하고)?")
_DOSE = re.compile(r"\d+(ml|mg|cc)", re.IGNORECASE)
# 다른 집 · 다른 보호자를 말하는 글. out_of_scope 조각에만 본다 — record 조각의 "친구네 집" 은
# 아이가 겪은 일이다
_OTHER_FAMILY = re.compile(r"(다른|남의|옆)\s*(집|아이|애|엄마|아빠|부모|가족|친구)")
# 다른 집이 나와도 우리에게 대신 해 달라고 시킨 말은 기록을 묻는 말이 아니다 — 대행 불가 문구 그대로
_DELEGATION = re.compile(r"주문|예약|결제|구매|연락|전화|문자|보내|전달|전해|신청")

# 한 번에 부를 수 있는 도메인 Agent 상한을 넘겨 빠진 요청이 있을 때 알리는 용도
# TODO: 사용자에게 제공될 답변 점검
AGENT_LIMIT = "agent_limit"
_AGENT_LIMIT = Guidance(
    code=AGENT_LIMIT,
    message=f"한 번에 {MAX_DOMAIN_AGENTS}가지까지 준비할 수 있어요. 나머지는 다시 말씀해 주세요.",
)


# 한 Agent 의 추천을 여러 가지 물어 뒤의 것을 뺐을 때 알리는 용도
TASK_LIMIT = "task_limit"
_TASK_LIMIT = Guidance(
    code=TASK_LIMIT,
    message="한 번에 한 가지씩 추천할 수 있어요. 나머지는 다시 말씀해 주세요.",
)


@dataclass(frozen=True)
class Routing:
    intent_type: IntentType
    # None은 SKIP_MEMORY_FOR_PURE_REQUEST가 켜졌거나 위험 물질 안내로 멈췄을 때만
    memory_task: MemoryTask | None
    domain_tasks: tuple[DomainTask, ...] = ()  # 구현된 agent 몫만
    unavailable_agents: tuple[str, ...] = ()  # 이동하려 했지만 아직 없는 agent
    dropped_agents: tuple[str, ...] = ()  # 호출 상한을 넘어 제외된 agent
    guidance: tuple[Guidance, ...] = ()
    degraded: bool = False  # Supervisor 실패 -> Memory 단독


def has_hazard(result: SupervisorResult) -> bool:
    """`guarded/hazard` 조각이 하나라도 있는가. Supervisor 가 실패했으면 False."""
    return result.output is not None and any(
        s.kind == SegmentKind.GUARDED and s.guard == GuardReason.HAZARD
        for s in result.output.segments
    )


def route(raw_text: str, result: SupervisorResult, *, run_id: str) -> Routing:
    """Supervisor 결과에서 이번 입력의 작업을 만든다."""
    if result.output is None:
        # 재시도하지 않고 Memory 단독으로 넘겨봄
        routing = Routing(
            intent_type="record", memory_task=MemoryTask(raw_text=raw_text), degraded=True
        )
        _log(routing, result.error)
        return routing

    segments = result.output.segments
    if has_hazard(result):
        # 위험 물질 섭취가 나온 입력은 아무것도 저장 · 추천하지 않는다 — 규칙 사전검사와 같다.
        # 같이 나온 기록 · 요청도 버린다. 아이 상태 확인이 먼저고, 섞어 처리하면 "좋아했다" 가
        # 다른 조각으로 기록될 수 있다
        routing = Routing(intent_type="record", memory_task=None, guidance=(HAZARD_GUIDANCE,))
        _log(routing, None)
        return routing
    records = [s for s in segments if s.kind == SegmentKind.RECORD]
    requests = [s for s in segments if s.kind == SegmentKind.REQUEST]

    hints = tuple(MemoryHint(text=s.text, work=WorkType(s.work)) for s in records)
    # unclear 조각을 넘기지 않으면 Memory 는 강등과 같은 입력을 받아 원문에서 다시 기록을 찾는다
    unclear = tuple(s.text for s in segments if s.kind == SegmentKind.UNCLEAR)
    # 기록 조각이 없어도 조각에 안 들어간 글자가 있으면 놓친 기록일 수 있어 다 막지 않는다
    unclear_only = not records and bool(unclear) and _covers_all(raw_text, result.output)
    # unclear 말고는 요청 · 안내뿐이면 답은 그쪽이 낸다.
    # 불러도 관찰은 다 거절되고 모델 문장만 나간다. 일정 초안은 만들 수 있지만
    # 이 입력에서는 그 기회를 포기한다 (#642)
    answered_elsewhere = unclear_only and any(s.kind != SegmentKind.UNCLEAR for s in segments)
    skip_memory = SKIP_MEMORY_FOR_PURE_REQUEST and (
        answered_elsewhere or covers_only_requests(raw_text, result.output)
    )
    memory_task = (
        None
        if skip_memory
        else MemoryTask(raw_text=raw_text, hints=hints, unclear=unclear, unclear_only=unclear_only)
    )

    # 요청한 agent는 처음 나온 순서대로, 상한까지만
    wanted = list(dict.fromkeys(str(s.agent) for s in requests))
    selected, dropped = wanted[:MAX_DOMAIN_AGENTS], wanted[MAX_DOMAIN_AGENTS:]

    domain_tasks, trimmed = _domain_tasks(
        [s for s in requests if str(s.agent) in selected and str(s.agent) in IMPLEMENTED_AGENTS],
        run_id=run_id,
        record_texts=tuple(s.text for s in records),
    )

    routing = Routing(
        intent_type=_intent(has_record=bool(records), has_request=bool(requests)),
        memory_task=memory_task,
        domain_tasks=domain_tasks,
        unavailable_agents=tuple(a for a in selected if a not in IMPLEMENTED_AGENTS),
        dropped_agents=tuple(dropped),
        guidance=_guidance(result.output, dropped=bool(dropped), trimmed=trimmed),
    )
    _log(routing, None)
    return routing


def _intent(*, has_record: bool, has_request: bool) -> IntentType:
    """의도 3형. guarded · out_of_scope · unclear는 영향이 없다."""
    if has_request:
        return "mixed" if has_record else "request"
    return "record"


def _domain_tasks(
    segments: list, *, run_id: str, record_texts: tuple[str, ...] = ()
) -> tuple[tuple[DomainTask, ...], bool]:
    """agent × task_type 당 하나. 처음 나온 순서를 따른다.

    `_ONE_RECOMMENDATION` 의 Agent 는 추천 라벨을 하나만 남긴다(`_kept_recommendations`). 뺀 조각이
    있으면 True — 호출부가 안내 한 줄을 붙인다(조용히 빠지면 사용자는 답이 없는 이유를 모른다).
    `record_texts` 는 같은 입력의 기록 조각이다 — 모든 task 에 같이 싣는다(코드 판정만 읽는다).
    """
    kept = _kept_recommendations(segments)
    texts: dict[tuple[str, str | None], list[str]] = {}
    said: dict[tuple[str, str | None], list[str]] = {}
    trimmed = False
    for segment in segments:
        agent, label = str(segment.agent), _task_label(segment)
        if label in _ONE_RECOMMENDATION.get(agent, frozenset()) and label != kept.get(agent):
            trimmed = True
            continue
        texts.setdefault((agent, label), []).append(_request_text(segment))
        said.setdefault((agent, label), []).append(segment.text)
    tasks = tuple(
        DomainTask(
            run_id=run_id,
            agent=agent,
            task_type=task_type,
            request_texts=tuple(items),
            record_texts=record_texts,
            # 해석한 문장이 든 task 만 보호자 원문을 따로 든다 — 아니면 request_texts 가 곧 원문이다
            original_texts=(
                () if said[(agent, task_type)] == items else tuple(said[(agent, task_type)])
            ),
        )
        for (agent, task_type), items in texts.items()
    )
    return tasks, trimmed


def _kept_recommendations(segments: list) -> dict[str, str]:
    """`_ONE_RECOMMENDATION` Agent 마다 이번 run 에 돌릴 추천 라벨 하나.

    처음 나온 것이되 `_YIELDING` 라벨은 다른 추천 라벨이 없을 때만 고른다 — "공룡 책이랑 양치 혼자
    하는 법" 에서 늘 닫히는 도서가 앞이라고 열리는 루틴까지 빠지지 않게.
    """
    kept: dict[str, str] = {}
    for agent, limited in _ONE_RECOMMENDATION.items():
        labels = [
            label
            for segment in segments
            if str(segment.agent) == agent and (label := _task_label(segment)) in limited
        ]
        if not labels:
            continue
        yielding = _YIELDING.get(agent, frozenset())
        kept[agent] = next((label for label in labels if label not in yielding), labels[0])
    return kept


def _request_text(segment) -> str:
    """도메인 task 가 받을 문장. 추천 task 만 해석한 요청을 받고 나머지는 보호자 원문 조각이다.

    확인 · 쓰기 task(food_check · daycare_meal · nutrient_analysis · growth_review · health 전부)는
    모델이 만든 문장을 받지 않는다 — 복약 대조 · 급식 갱신 · "먹여도 되나" 판정에 LLM 이 쓴 이름이
    들어가면 안 된다. 해석이 없으면 원문 조각이다.
    """
    resolved = getattr(segment, "resolved_request", None)
    if resolved and _task_label(segment) in RESOLVABLE_TASKS.get(str(segment.agent), frozenset()):
        return resolved
    return segment.text


def _task_label(segment) -> str | None:
    """Supervisor 가 agent 별로 붙이는 라벨. activity 는 라벨이 없다.

    health 는 라벨이 비어 있을 수 있다. 채우지 않고 None 으로 넘겨 Health 가 unsupported 로 닫게
    한다 — 짐작한 라벨이 복약 쓰기 경로를 열면 안 된다.
    """
    if segment.agent == DomainAgentName.FOOD:
        return str(segment.food_task)
    if segment.agent == DomainAgentName.GROWTH:
        return str(segment.growth_task)
    if segment.agent == DomainAgentName.HEALTH:
        return None if segment.health_task is None else str(segment.health_task)
    return None


def _guidance(
    output: SupervisorOutput, *, dropped: bool = False, trimmed: bool = False
) -> tuple[Guidance, ...]:
    """guard · out_of_scope 별 안내. 같은 코드는 한 번만.

    상한을 넘겨 버린 요청이 있으면 그것도 알린다 — 조용히 빠지면 사용자는 답이 없는 이유를 모른다.
    """
    texts: dict[str, list[str]] = {}
    for segment in output.segments:
        if segment.kind == SegmentKind.GUARDED:
            texts.setdefault(str(segment.guard), []).append(segment.text)
        elif segment.kind == SegmentKind.OUT_OF_SCOPE:
            texts.setdefault(SegmentKind.OUT_OF_SCOPE.value, []).append(segment.text)
    guidance = [_pick_guidance(code, said) for code, said in texts.items()]
    if dropped:
        guidance.append(_AGENT_LIMIT)
    if trimmed:
        guidance.append(_TASK_LIMIT)
    return tuple(guidance)


def _pick_guidance(code: str, said: list[str]) -> Guidance:
    """같은 code 의 문구 두 벌 중 조각 글에 맞는 쪽 (#864). 한 조각이라도 걸리면 그쪽이다."""
    if code == GuardReason.DIAGNOSIS.value:
        return (
            _GUIDANCE[code]
            if any(mentions_treatment(t) for t in said)
            else PLAIN_DIAGNOSIS_GUIDANCE
        )
    if code == SegmentKind.OUT_OF_SCOPE.value and any(mentions_other_family(t) for t in said):
        return OTHER_FAMILY_GUIDANCE
    return _GUIDANCE[code]


def mentions_treatment(text: str) -> bool:
    """글에 약 · 영양제 · 치료식 얘기가 있는가. 낱말 단위다 — "예약" 의 "약" 은 약이 아니다."""
    for word in re.findall(r"\w+", text):
        if _BARE_MEDICINE.fullmatch(word) or _DOSE.fullmatch(word):
            return True
        if word.startswith(tuple(_TREATMENT_STEMS)):
            return True
    return False


def mentions_other_family(text: str) -> bool:
    """글이 다른 집 아이 · 다른 보호자의 기록 · 하는 일을 묻는가. 대신 해 달라는 말은 아니다."""
    return _OTHER_FAMILY.search(text) is not None and _DELEGATION.search(text) is None


def covers_only_requests(raw_text: str, output: SupervisorOutput) -> bool:
    """원문의 내용 글자가 전부 request · guarded · out_of_scope 조각에 들어가는가.

    참이면 기록할 게 없는 순수 요청형이다 — Memory를 건너뛸 후보.
    RECORD · UNCLEAR 조각이 하나라도 있거나, 어느 조각에도 안 들어간 글자가 있으면
    Supervisor 가 놓친 기록일 수 있다.
    """
    if any(s.kind in (SegmentKind.RECORD, SegmentKind.UNCLEAR) for s in output.segments):
        return False
    return _covers_all(raw_text, output)


def _covers_all(raw_text: str, output: SupervisorOutput) -> bool:
    """원문의 내용 글자가 전부 어느 조각에든 들어가는가."""
    haystack = normalize(raw_text)
    covered = [False] * len(haystack)
    for segment in output.segments:
        for start, end in align_segment(raw_text, segment) or []:
            for index in range(start, end):
                covered[index] = True
    for word in re.finditer(r"\w+", haystack):
        span = covered[word.start() : word.end()]
        if all(span):
            continue
        # 낱말이 통째로 조각 밖이고 기록일 수 없는 말이면 덮인 것으로 본다.
        # 일부만 덮인 낱말은 아니다
        if not any(span) and _is_filler(word.group()):
            continue
        return False
    return True


def _is_filler(word: str) -> bool:
    return word in FILLER_WORDS or _JAMO_ONLY.fullmatch(word) is not None


def _log(routing: Routing, supervisor_error: str | None) -> None:
    """원문·조각 없이 개수와 라벨만"""
    logger.info(
        "routing intent=%s degraded=%s supervisor_error=%s hints=%d unclear=%d lookup_edit=%s "
        "domain_tasks=%s unavailable=%s dropped=%s guidance=%s memory=%s",
        routing.intent_type,
        routing.degraded,
        supervisor_error,
        len(routing.memory_task.hints) if routing.memory_task else 0,
        len(routing.memory_task.unclear) if routing.memory_task else 0,
        routing.memory_task.open_lookup_edit if routing.memory_task else False,
        [f"{task.agent}:{task.task_type}" for task in routing.domain_tasks],
        list(routing.unavailable_agents),
        list(routing.dropped_agents),
        [guidance.code for guidance in routing.guidance],
        routing.memory_task is not None,
    )
