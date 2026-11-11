"""Growth tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 월령, 동의)과 날짜 계산의 기준(now, timezone), 읽기 포트를
한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.

월령은 context가 값으로 들고 있지 않는다. `build_gate()`가 매 run
`ports.profile.birth_date`에서 `LifeStage`를 계산해 `Gate`로 넘긴다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.age import life_stage
from app.agents.common.datetime_rules import today_of
from app.agents.common.evidence import RankedEvidence
from app.agents.common.gate import DataReady, Gate, SafetyState
from app.agents.common.suggestion import MAX_SUGGESTIONS, SuggestionDraft
from app.agents.growth.gating import EDUCATION_MIN_MONTH
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.ports import (
    BookRow,
    GrowthDocRow,
    GrowthMeasurement,
    GrowthPorts,
    RoutineObservation,
    SafetyEntry,
    SafetyLookupError,
    UnwiredSafety,
)
from app.agents.growth.tools.routine import PickedStep
from app.agents.growth.tools.safety import SafetyRules, SafetyRulesUnavailable, load_safety_rules


@dataclass
class GrowthRunState:
    """run 하나 동안만 사는 값. `build_gate` 가 읽은 것을 담고 tool 이 읽는다.

    건강정보 · 측정은 **run 당 한 번만 읽는다.** 출력 검증이 다시 읽으면 같은 run 안에서 게이트와
    필터가 다른 행을 볼 수 있다. 테스트가 읽기 횟수를 고정한다.
    """

    gate: Gate | None = None
    birth_date: date | None = None
    # growth_review + 동의가 있을 때만. 시간순 정렬은 compute_growth_delta 가 한다
    measurements: tuple[GrowthMeasurement, ...] = ()
    # learning_suggestion + 동의가 있을 때만. status 무관 전체 — 거르는 것은 안전 필터다
    safety_entries: tuple[SafetyEntry, ...] = ()
    # learning_suggestion · routine_coaching. 안전 사전과 매처
    rules: SafetyRules | None = None
    # book_suggestion. 이번 run 에서 `search_books` 가 돌려준 책 (정규화한 ISBN → 행).
    # `propose_books` 는 이 표에 있는 ISBN 만 통과시킨다 — 연령 필터에 걸러진 책은 들어오지 않는다
    seen_books: dict[str, BookRow] = field(default_factory=dict)
    # book_suggestion. 이번 run 에서 search_books 를 한 번이라도 불렀는가 — 0권 제출은 검색한 뒤에만
    # 받는다. 찾아보지도 않고 "추천이 없어요" 를 내지 않게
    book_searched: bool = False
    # book_suggestion. 이번 run 에서 정보나루로 보낸 검색어 수 — `MAX_RUN_KEYWORDS` 까지
    book_keywords: int = 0
    # book_suggestion. 이번 run 에서 보호자가 요청에 말한 검색 낱말(정규화). 있으면 그 낱말로 제목을
    # 찾고, 제목에 이 낱말이 없는 책은 낼 수 없다. 다른 주제 책으로 채우지 않는다
    asked_book_words: frozenset[str] = frozenset()
    # 재호출 전에 이미 통과했거나 빠진 책(정규화한 "제목(저자)"). 재호출은 이 책을 다시 낼 수 없다
    done_books: frozenset[str] = frozenset()

    # ── 모델 경로 ──
    # 이번 run 에서 조회 tool 이 돌려준 근거 (id → 근거). 이 표 밖 id 는 출력 tool 이 거절한다
    seen_evidence: dict[UUID, RankedEvidence] = field(default_factory=dict)
    # search_routine_memory 가 돌려준 루틴 관찰. 근거 14일 · 카테고리 대조에 쓴다
    seen_routine: dict[UUID, RoutineObservation] = field(default_factory=dict)
    # 요청 주제의 아이 기록. 코드가 [예시]를 고른 직후 모델 전에 읽는다
    # (`tools/memory.topic_records`).
    # 프롬프트에 싣고 `seen_evidence` 에도 넣어 조회 없이 근거로 달 수 있다. 이 기록이 있으면 후보는
    # 하나 이상을 달아야 한다(`review.review_learning`). 주제가 없는 요청은 비어 있다
    topic_records: dict[UUID, RankedEvidence] = field(default_factory=dict)
    # `topic_records` 를 안 달아 한 번 거절됐는가. 두 번째 제출부터는 강제하지 않는다 — 막기만 하면
    # 모델이 같은 실수를 되풀이해 왕복 상한에 걸리고 카드가 하나도 안 나간다(루트 §2 부분 결과)
    topic_record_retried: bool = False
    # `tools/memory._citable` 이 읽은 근거 후보. run 당 한 번만 읽는다 — 선조회 · 닫힘 판정 · 되묻기
    # 판정이 같은 표를 본다. Memory 쓰기는 이 run 이 돌기 전에 끝난다(루트 §4)
    citable: tuple[RankedEvidence, ...] | None = None
    # 프롬프트 [예시]에 실은 문서 행. 일반 추천은 이 행을 근거로 단다 (루트 §2)
    seen_docs: dict[UUID, GrowthDocRow] = field(default_factory=dict)
    # 보호자가 주제를 정해 물었는데 그 주제의 [예시] 행이 없는 교육 run. 코드가 [예시]를 고른 직후
    # 한 번 정한다. 이때 일반 추천은 문서 행이 아니라 그 요청이 기준이다(`review_learning`)
    requested: bool = False
    # 요청 · 같은 입력의 기록 조각에서 코드가 읽은 주제 영역 키(`rules/growth_topic`).
    # 모델이 정하지 않는다. [예시]를 고르고 기억 조회를 넓히는 데 쓴다. 비면 주제가 없는 요청이다
    topic: tuple[str, ...] = ()
    # `topic` 중 후보 글의 낱말로 거를 수 있는 영역(`rules/growth_topic.strict_topic`). 교육 후보의
    # 주제 확인에만 쓴다. 비면 거르지 않는다
    strict_topic: tuple[str, ...] = ()
    # 보호자가 말한 루틴 카테고리 키(`rules/growth_topic.routine_scope`). 모델이 다른 카테고리의
    # 단계를 내지 못하게 한다. 비면 범위를 정하지 않는다
    routine_scope: tuple[str, ...] = ()
    # 보호자가 말한 루틴 낱말(`rules/growth_topic.routine_words`). 행이 없는 루틴 행동을 교육
    # 활동으로 낼 때(`agent._run` 의 `from_routine`) 개인화 근거를 이름으로 맞춘다. 평소엔 비어 있다
    routine_words: tuple[str, ...] = ()
    # 보호자가 이 입력에서 한 말(요청 조각 + 같은 입력의 기록 조각)과, 같은 run 에서 Memory 가
    # 저장한 루틴 관찰의 행동 이름. 쪼개진 요청에서 보호자가 말한 행동을 코드가 알아
    # 모델이 다른 행동을 적는 것을 막는다
    said_texts: tuple[str, ...] = ()
    recorded_subjects: tuple[str, ...] = ()
    # 관찰 subject(정규화) → 코드가 고른 다음 단계. 모델은 여기 있는 step_id 만 쓴다
    steps: dict[str, PickedStep] = field(default_factory=dict)
    # 출력 tool 이 통과시킨 추천과 안전 필터 · 금지 표현으로 빠진 후보 이름 (재호출 제외 목록)
    suggestions: tuple[SuggestionDraft, ...] = ()
    excluded: tuple[str, ...] = ()
    # 이번 출력이 채울 수 있는 최대 개수. 루틴은 고른 단계 수가 상한이다
    capacity: int = MAX_SUGGESTIONS
    # 재호출 전에 이미 통과한 루틴 단계. 재호출은 남은 단계만 다시 내면 된다
    done_steps: frozenset[UUID] = frozenset()
    # 추천 없이 끝내는 코드 문구 키 (닫힘 · doc.no_row · next_step.chain_end)
    closing: str | None = None
    # 되묻기 한 줄 (needs_observation)
    question: str | None = None
    # 자립 카테고리별 단계 사슬. 사슬 찾기 · 자료 없음 판정 · 저장 이름이 같은 것을 다시 읽지 않는다
    chains: dict[str, dict[str, tuple[GrowthDocRow, ...]]] = field(default_factory=dict)
    # 되묻기를 낸 자리 (되묻기 키, 저장 이름, 카테고리). 답을 이어받는 Memory 가 어느 행동의 어느
    # 칸에 저장할지 코드가 알게 한다
    asked: tuple[str, str, str] | None = None
    # 되묻기에 답을 받고 이어서 도는 run 이면 그 행동의 저장 이름. 그 행동은 수준을 못 읽어도
    # 다시 묻지 않는다
    answered_subject: str | None = None

    # ── 관측 로그 (agent.run 이 끝날 때 한 줄) ──
    # 추천 없이 끝낸 코드 문구 키 — closed.* · blocked.safety · doc.no_row · rhythm.no_row …
    closed_reason: str | None = None
    # 시작한 대화 수. 20초에 끊겨 결과가 없을 때 몇 번째 대화에서 끊겼는지 남긴다
    model_calls: int = 0
    # 이 run 이 실제로 돈 라벨. 화면 고민은 루틴 라벨로 와도 교육으로 돈다(`agent._run`)
    task_type: str | None = None

    def reset(self) -> None:
        """모든 칸을 처음 값으로 돌린다. 루틴 대화가 자료 없음으로 끝나 교육 흐름으로 넘어갈 때,
        루틴이 채운 근거 · 단계 · 닫힘 키가 교육 판정에 섞이지 않게 한다. 호출부가 남길 값(시작한
        대화 수)은 먼저 따로 챙긴다."""
        fresh = GrowthRunState()
        for item in fields(self):
            setattr(self, item.name, getattr(fresh, item.name))


@dataclass(frozen=True)
class GrowthContext:
    child_id: UUID
    run_id: str
    now: datetime  # timezone이 붙은 현재 시각
    timezone: tzinfo
    ports: GrowthPorts
    # 안전 사전을 읽는 함수. 기본은 사전과 공용 판정으로 만든 load_safety_rules (tools/safety.py)
    safety_rules: Callable[[], SafetyRules] = load_safety_rules
    state: GrowthRunState = field(default_factory=GrowthRunState)

    @property
    def today(self) -> date:
        return today_of(self.now, self.timezone)

    def for_task(self) -> "GrowthContext":
        """task 하나 몫의 context. run state 만 새로 만든다.

        pipeline 이 Agent 를 부르기 전에 늘 부른다 (`DomainContext` 프로토콜). task 둘이 동시에
        돌아도 측정 · 안전 행이 섞이지 않는다.
        """
        return replace(self, state=GrowthRunState())


async def read_learning_safety(context: GrowthContext, *, consent: bool) -> bool:
    """교육 후보를 거를 안전 사전과 등록 알레르기를 읽어 run state 에 담는다. 읽었으면 True.

    - 안전 포트가 `UnwiredSafety` 면 동의와 상관없이 False 다 — 어댑터가 붙기 전에는 알레르기 확인
      없이 열지 않는다(Activity #380 과 같다).
    - 사전을 못 읽거나 조회가 실패해도 False 다. 빈 사전 · 빈 목록으로 통과시키지 않는다.
    - 동의가 없으면 건강정보를 읽지 않고 True 다 — 읽을 것이 없는 상태이지 실패가 아니다.

    `build_gate` 의 교육 분기와 일반 정보 질문(`growth/info`)이 같은 읽기를 쓴다.
    """
    state, ports = context.state, context.ports
    if isinstance(ports.safety, UnwiredSafety):
        return False
    try:
        state.rules = context.safety_rules()
        entries = await ports.safety.growth_safety(child_id=context.child_id) if consent else []
    except (SafetyRulesUnavailable, SafetyLookupError):
        state.rules = None
        return False
    state.safety_entries = tuple(entries)
    return True


async def build_gate(
    context: GrowthContext, task_type: GrowthTaskType, *, book_api_ok: bool = True
) -> Gate:
    """이번 run의 Gate(registry `tools_for`의 입력).

    - birth_date → `life_stage()`로 `LifeStage`를 계산한다. 월령은 보호자 시간대의 오늘에서 센다.
    - 건강정보는 **라벨이 필요할 때만** 읽는다. 동의가 없으면 아예 읽지 않는다.
        growth_review      동의가 있을 때 측정 **전부**(`child_growth_log`)
        learning_suggestion 동의가 있을 때 `health_safety`(allergy · environmental)
        그 밖                읽지 않는다 — 루틴 · 도서는 건강정보를 쓰지 않는다
    - 읽다가 실패하면 빈 목록이 아니라 닫는다. 안전 조회 실패는 `safety_ok=False` 다
      (교육만 닫힌다).
      안전 포트가 `UnwiredSafety` 면 동의와 상관없이 교육을 닫는다.
      안전 사전을 못 읽은 것도 같다 — 빈 사전으로 통과시키지 않는다. 측정 읽기 실패는 예외를 그대로
      올린다 ("측정 0건" 으로 읽히면 안 된다).
    - 교육이 0–11개월이면 어차피 `closed.infant_learning` 으로 닫혀서 안전 정보도 읽지 않는다.
    - `book_api_ok` 는 호출부가 도서 API 상태를 넘긴다. 도서 포트(인증키)가 없으면 닫힌다.
    """
    ports = context.ports
    state = context.state
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    stage = life_stage(birth_date, context.today)
    consent = await ports.consent.child_health_granted(child_id=context.child_id)
    state.birth_date = birth_date

    safety_ok = True
    allergy_states: tuple[SafetyState, ...] = ()
    growth_log_count = 0
    notice = False

    if task_type is GrowthTaskType.LEARNING_SUGGESTION and stage.months >= EDUCATION_MIN_MONTH:
        safety_ok = await read_learning_safety(context, consent=consent)
        allergy_states = tuple(
            entry.status for entry in state.safety_entries if entry.kind == "allergy"
        )
        if ports.notice is not None:
            notice = await ports.notice.has_notice(child_id=context.child_id)
    elif task_type is GrowthTaskType.ROUTINE_COACHING:
        state.rules = context.safety_rules()  # 음식 용어 스캔용. 실패는 그대로 올린다
    elif task_type is GrowthTaskType.GROWTH_REVIEW and consent:
        measurements = await ports.growth_log.measurements(child_id=context.child_id)
        state.measurements = tuple(measurements)
        growth_log_count = len(measurements)

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=safety_ok,
        allergy_states=allergy_states,
        growth_log_count=growth_log_count,
        data=DataReady(notice=notice, book_api=book_api_ok and ports.books is not None),
    )
