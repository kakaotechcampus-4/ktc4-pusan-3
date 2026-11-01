"""API가 부르는 agents 진입점.

api 레이어는 이 모듈만 import한다. 밖으로는 handle_input · suggest 와 그 입출력·이벤트 타입, api 가
응답을 만들 때 쓰는 판정(사전검사 · 문서 행 · 안내 문구)만 내보낸다.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents import pipeline
from app.agents.activity.context import ActivityContext
from app.agents.activity.store.inmemory import InMemoryConsent as ActivityConsent
from app.agents.activity.store.inmemory import InMemoryProfile as ActivityProfile
from app.agents.activity.store.inmemory import unwired_ports as activity_unwired_ports
from app.agents.activity.store.ports import CoarseLocation, PlaceSource, WeatherSource
from app.agents.activity.store.ports import SafetyReader as ActivitySafetyReader
from app.agents.common.allergy import allergen_states, shared_book
from app.agents.common.child_values import ChildValues
from app.agents.common.datetime_rules import today_of
from app.agents.common.readout import Readout
from app.agents.common.reference_docs import doc_row_id
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import (
    MAX_SUGGESTIONS,
    cannot_resume_notice,
    count_notice,
    empty_notice,
)
from app.agents.curator.embedding.embed_step import TextEmbedder
from app.agents.curator.embedding.judge import IdentityJudge
from app.agents.food.context import FoodContext
from app.agents.food.store import InMemoryFoodDocs, InMemoryProfile, unwired_ports
from app.agents.food.store.ports import (
    MenuCatalogStore,
    RecipeCatalogStore,
    SuggestionHistoryReader,
)
from app.agents.food.store.ports import SafetyReader as FoodSafetyReader
from app.agents.growth.context import GrowthContext
from app.agents.growth.library import LibraryBooks
from app.agents.growth.store.inmemory import InMemoryConsent as GrowthConsent
from app.agents.growth.store.inmemory import InMemoryGrowthDocs, InMemoryGrowthLog
from app.agents.growth.store.inmemory import InMemoryProfile as GrowthProfile
from app.agents.growth.store.ports import (
    GrowthLogReader,
    GrowthPorts,
    IssuedBookReader,
    RawBookSearch,
)
from app.agents.growth.store.ports import SafetyReader as GrowthSafetyReader
from app.agents.growth.store.ports import UnwiredSafety as GrowthUnwiredSafety
from app.agents.memory.agent import MemoryReply
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.schemas.task import PendingMemoryContext
from app.agents.memory.store import InMemoryStore, MemoryStore
from app.agents.memory_bridge import (
    AffinityReader,
    StoreActivityMemory,
    StoreFoodMemory,
    StoreGrowthMemory,
    StoreOpener,
)
from app.agents.pipeline import (
    RECOMMENDING_TASKS,
    AgentQuestion,
    AgentResult,
    Commit,
    CountRecords,
    CuratorTrigger,
    DomainContext,
    DomainRouted,
    Done,
    Emit,
    EmptyResult,
    Event,
    EventDrafts,
    Failed,
    MemoryNote,
    Partial,
    PendingReply,
    PipelineResult,
    Ref,
    Rerouted,
    RetryContext,
    Saved,
    Step,
    SuggestOutcome,
    SuggestRun,
    Unavailable,
    Unwritten,
)
from app.agents.pipeline import handle_input as _handle_input
from app.agents.run_writes import RunWrites, record_food_writes
from app.agents.suggestion_save import InMemorySuggestions, SaveSuggestions, SuggestionBatch
from app.agents.supervisor.routing import Guidance

# api가 이 파일만 보면 되도록 진행 이벤트 타입도 여기서 내보냄
# pipeline에 이벤트를 추가하면 여기에도 작성
__all__ = [
    "MAX_SUGGESTIONS",
    "FIRST_REQUEST_AGENTS",
    "SUGGESTION_AGENTS",
    "AffinityReader",
    "AgentQuestion",
    "AgentResult",
    "AllergenCheck",
    "CoarseLocation",
    "Commit",
    "CountRecords",
    "CuratorTrigger",
    "DocCard",
    "DomainRouted",
    "Done",
    "Emit",
    "EmptyResult",
    "Event",
    "EventDraft",
    "EventDrafts",
    "Failed",
    "Guidance",
    "GrowthLogReader",
    "InMemoryStore",
    "InMemorySuggestions",
    "IssuedBookReader",
    "MemoryNote",
    "MemoryReply",
    "MemoryStore",
    "Partial",
    "PendingMemoryContext",
    "PendingReply",
    "PipelineResult",
    "PlaceSource",
    "RawBookSearch",
    "Readout",
    "Ref",
    "Rerouted",
    "RetryContext",
    "SafetyLabel",
    "SaveSuggestions",
    "Saved",
    "Step",
    "StoreOpener",
    "SuggestOutcome",
    "SuggestRun",
    "SuggestTask",
    "SuggestionBatch",
    "Unavailable",
    "Unwritten",
    "WeatherSource",
    "allergy_cautions",
    "cannot_resume_notice",
    "check_allergens",
    "count_notice",
    "curator_deps",
    "describe_docs",
    "empty_notice",
    "handle_input",
    "recommendation_task_type",
    "suggest",
]

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 생일을 못 받았을 때만 쓰는 fallback. 만 2세로 가정해 확실히 toddler 단계로 연다
# (LifeStage.stage 네 값 중 toddler·preschool 은 Food 의 tool 묶음이 같다)
_FALLBACK_BIRTH_OFFSET = timedelta(days=365 * 2)


@cache
def curator_deps() -> tuple[TextEmbedder | None, IdentityJudge | None]:
    """러너가 Curator 트리거를 만들 때 쓰는 embedder · judge. 키가 없으면 그 자리가 None.

    embedder 가 None 이면 러너는 트리거를 넘기지 않는다(Curator 를 띄우지 않는다).
    judge 만 None 이면 Curator 는 임베딩 · 보류까지만 한다.
    한 번 만들면 프로세스가 살아 있는 동안 재사용한다 — HTTP 클라이언트를 매 run 만들면
    연결 풀이 낭비되고 누수 가능성이 있다.
    """
    from app.agents.common.llm_client import LLMConfigError
    from app.agents.curator.embedding.embedder import Embedder
    from app.agents.curator.embedding.jev import JevJudge

    embedder: TextEmbedder | None = None
    judge: IdentityJudge | None = None
    try:
        embedder = Embedder()
    except LLMConfigError:
        logger.debug("EMBEDDING_* 미설정 — curator embedder 비활성")
    try:
        judge = JevJudge()
    except LLMConfigError:
        logger.debug("CURATOR_JUDGE_* 미설정 — curator judge 비활성")
    return embedder, judge


async def handle_input(
    *,
    child_id: UUID,
    parent_id: UUID,
    raw_text: str,
    run_id: str,
    birth_date: date | None = None,
    child_health_granted: bool | None = None,
    location: CoarseLocation | None = None,
    activity_weather: WeatherSource | None = None,
    activity_places: PlaceSource | None = None,
    emit: Emit | None = None,
    store: MemoryStore | None = None,
    continuation: PendingMemoryContext | None = None,
    commit: Commit,
    save_suggestions: SaveSuggestions,
    curator_trigger: CuratorTrigger | None = None,
    domain_store: StoreOpener | None = None,
    count_records: CountRecords | None = None,
    food_catalog: MenuCatalogStore | None = None,
    food_recipes: RecipeCatalogStore | None = None,
    food_suggestions: SuggestionHistoryReader | None = None,
    food_safety: FoodSafetyReader | None = None,
    activity_safety: ActivitySafetyReader | None = None,
    growth_safety: GrowthSafetyReader | None = None,
    growth_log: GrowthLogReader | None = None,
    growth_books: RawBookSearch | None = None,
    growth_issued_books: IssuedBookReader | None = None,
    affinity_reader: AffinityReader | None = None,
) -> PipelineResult:
    """입력 한 줄을 처리한다. 진행 상황은 emit 으로 나간다.

    - birth_date를 넘기면 Food Agent가 build_gate에서 그 값으로 식이 단계를 계산한다.
    - child_health_granted 는 API 가 run 마다 한 번 읽은 `child_health` 동의의 현재 값이다.
      생일과 같이 값을 감싼 포트(`ChildValues`)로 Food 의 `ConsentReader` 에 들어간다. 읽는 데
      실패하면 API 가 run 을 시작하지 않으므로 여기에는 실패 값이 없다 — None 은 값을 안 넘긴
      호출(테스트 · eval)이고 "동의 안 함" 으로 바뀌어 읽히지 않는다.
    - location 은 휴대폰이 흐려 보낸 좌표다(#380). `CoarseLocation` 이 소수점 둘째 자리로 한 번 더
      자르고, repr 에 값을 싣지 않는다. 이 run 안에서 격자 변환과 장소 거리 계산에만 쓰고 버린다 —
      저장 · 로그 · 모델 입력에 넣지 않는다 (activity-agent-v1 4-3). None 이면 Activity 는
      실내 전용이다.
    - activity_weather 는 api 가 서버 시작 때 만든 날씨 어댑터다. agents 는 integrations 를
      import 하지 않아서 api 가 넘긴다. None 이면 날씨를 묻지 않고 실내 전용이다.
    - activity_places 는 api 가 넘긴 장소 어댑터(적재한 `place` 표)다. None 이거나 좌표가 없으면
      장소 조회가 닫혀 장소가 필요 없는 활동만 나간다.
    - store를 넘기면 그것에 쓴다.
    - agents 는 infra 에 직접 닿지 않는다. store · commit · curator_trigger 처럼 DB 에 닿는 것은
      전부 api(러너)가 만들어 넘긴다.
    - continuation을 넘기면 raw_text는 이전 질문에 대한 보호자의 답이다. api는 reply_to를
      검증·복원한 뒤 이 값만 넘긴다.
    - commit 은 Memory 가 끝난 직후 한 번 부른다(필수). 기록 단계(Memory 의 쓰기)를
      확정하는 함수이고, 러너가 run 마다 만든다. 저장 안내는 그 뒤에 나간다. 되묻기 맥락은
      같은 트랜잭션에 넣지 않는다 — commit 뒤 PendingReply 로 나가 러너가 프로세스 메모리에
      둔다(팀 합의). Memory 가 반복 상한에 걸려 기록을 다 끝내지 못하면 commit 을 부르지
      않는다. 러너는 commit 되지 않은 세션을 되돌린다.
    - save_suggestions 는 도메인 Agent 결과 하나의 추천을 저장 · commit 하는 함수다(필수). 러너가
      결과마다 짧은 DB 세션으로 만든다. 테스트 · eval 은 `InMemorySuggestions()`.
    - curator_trigger 를 넘기면 Memory 가 commit 된 직후 pipeline 이 (child_id, 기준일) 로 한 번
      부른다. 콜백이 별도 세션에서 Curator 를 백그라운드로 띄운다. 러너가 `curator_deps()` 로
      만든다. 안 넘기면(테스트 · eval) Curator 는 돌지 않는다.
    - growth_books · growth_issued_books 는 api 가 넘긴 도서 어댑터다 — 정보나루 검색(검색어 캐시
      포함)과 만료 전 Growth 추천의 "제목(저자)". 둘 다 있어야 도서가 열린다. 하나라도 없으면(키
      없음 · 테스트 · eval) `closed.book_api` 로 닫힌다 — 낸 책을 모르면 같은 책이 다시 나온다.
    - growth_log 는 러너가 짧은 DB 세션으로 만든 성장 기록 어댑터다. 안 넘기면(테스트 · eval)
      측정이 없는 것으로 읽힌다.
    - domain_store 를 넘기면 도메인 Agent 의 기억 포트가 호출마다 그것으로 store 를 새로 연다.
      러너가 짧은 DB 세션으로 만든다 — run 세션을 도메인 task 들이 같이 타지 않게. 안 넘기면
      run 의 store 를 그대로 읽는다(테스트 · eval 의 InMemoryStore).
    - count_records 를 넘기면 일반 추천 블록의 쌓인 기록 수를 센다. 러너가
      `agent_ports.record_counter` 로 만든다. 안 넘기면(테스트 · eval) 그 칸이 빈다.
    - food_catalog · food_recipes · food_suggestions 는 api 가 넘긴 Food 카탈로그 · 추천 이력
      어댑터다(`domains/food` · `domains/suggestion` 의 `agent_reader`). 안 넘기면(테스트 · eval)
      빈 InMemory 다. 문서 행은 어댑터가 아니라 yaml 의 검수된 시드를 항상 읽는다.
    - food_safety · activity_safety · growth_safety 는 api 가 넘긴 `health_safety` 어댑터다
      (`DbSafetyReader`, #405 · #460). 같은 객체여도 Food 는 `food_safety`, Activity 는
      `activity_safety`, Growth 는 `growth_safety` 로 읽는다 — 읽는 행이 다르다. 안 넘기면(테스트 ·
      eval) `UnwiredSafety` 라 동의와 상관없이 닫힌다 — Growth 는 교육만 닫힌다.
    - affinity_reader 는 api 가 넘긴 `profile_affinity` 어댑터다(`DbAffinityReader`). 기억 포트가 그
      run 의 기준일과 함께 받아, 마지막 관찰 뒤 21일이 지난 관심은 archived 로 읽는다. 안 넘기면
      (테스트 · eval) 관심이 없는 아이로 읽는다.
    """
    now = datetime.now(KST)
    # 안 넘기면 run 하나가 store 하나를 쓴다. 앞선 run에 저장한 관찰은 다음 run에서
    # 조회되지 않고, 저장된 행을 api가 다시 읽을 곳도 존재하지 않다
    if store is None:
        store = InMemoryStore(now=now)

    memory_context = AgentContext(
        child_id=child_id,
        source_writer=parent_id,
        now=now,
        timezone=KST,
        store=store,
    )

    contexts, writes = _domain_contexts(
        child_id=child_id,
        run_id=run_id,
        now=now,
        store=store,
        birth_date=birth_date,
        child_health_granted=child_health_granted,
        location=location,
        activity_weather=activity_weather,
        activity_places=activity_places,
        domain_store=domain_store,
        food_catalog=food_catalog,
        food_recipes=food_recipes,
        food_suggestions=food_suggestions,
        food_safety=food_safety,
        activity_safety=activity_safety,
        growth_safety=growth_safety,
        growth_log=growth_log,
        growth_books=growth_books,
        growth_issued_books=growth_issued_books,
        affinity_reader=affinity_reader,
    )

    return await _handle_input(
        raw_text,
        memory_context,
        contexts,
        run_id=run_id,
        emit=emit,
        continuation=continuation,
        commit=commit,
        save_suggestions=save_suggestions,
        writes=writes,
        curator_trigger=curator_trigger,
        count_records=count_records,
    )


@dataclass(frozen=True)
class SuggestTask:
    """05 가 돌릴 추천 task 하나. 요청 문장은 서버가 정한 것만 — 홈 버튼 키의 표 문장이거나
    입력 run 이 남긴 맥락이다. 화면이 보낸 문장은 들어오지 않는다 (#249 · #286)."""

    agent: str
    task_type: str | None
    request_texts: tuple[str, ...]


SUGGESTION_AGENTS: frozenset[str] = frozenset(RECOMMENDING_TASKS)
# 05 처음 요청(run_id 없음)으로 돌 수 있는 Agent — 추천 라벨이 하나라 무엇을 돌릴지 정해진다.
# Growth 는 라벨이 셋이라 처음 요청은 not_ready 다. 홈 버튼을 붙일 때 그 표가 라벨까지 정한다
FIRST_REQUEST_AGENTS: frozenset[str] = frozenset(
    agent for agent, labels in RECOMMENDING_TASKS.items() if len(labels) == 1
)


def recommendation_task_type(agent: str) -> str | None:
    """05 가 처음 요청으로 돌릴 때의 라벨. `FIRST_REQUEST_AGENTS` 만 부른다."""
    [label] = RECOMMENDING_TASKS[agent]
    return label


async def suggest(
    *,
    child_id: UUID,
    tasks: Sequence[SuggestTask],
    request_id: str,
    save_run_id: str | None,
    sent_at: datetime | None,
    save_suggestions: SaveSuggestions,
    birth_date: date | None = None,
    child_health_granted: bool | None = None,
    domain_store: StoreOpener | None = None,
    store: MemoryStore | None = None,
    food_catalog: MenuCatalogStore | None = None,
    food_recipes: RecipeCatalogStore | None = None,
    food_suggestions: SuggestionHistoryReader | None = None,
    food_safety: FoodSafetyReader | None = None,
    activity_safety: ActivitySafetyReader | None = None,
    growth_safety: GrowthSafetyReader | None = None,
    growth_log: GrowthLogReader | None = None,
    growth_books: RawBookSearch | None = None,
    growth_issued_books: IssuedBookReader | None = None,
    affinity_reader: AffinityReader | None = None,
) -> SuggestRun:
    """05 추천 요청 하나 (#249). 진행 이벤트는 없다 — 응답 하나로 끝난다.

    - `request_id` 는 05 요청 하나의 id 다. Agent 의 run_id(Food 샘플 seed)로 쓴다 — 다시 만들 때
      입력 run 과 같은 후보가 다시 뽑히지 않게.
    - `save_run_id` 는 저장할 추천의 run 칸이다. 입력 run 을 다시 만들면 그 run, 홈 · 04 제안이면
      None.
    - `sent_at` 은 입력 run 의 기준 시각이다. 다시 만들 때 "내일 아침" 이 그때 기준으로 읽혀야 해서
      Agent context 의 now 로만 쓴다. 새 추천의 `expires_at` 은 저장할 때 실제 시각으로 잡힌다.
      None 이면 지금이다.
    - 생일 · 동의 · 포트는 `handle_input` 과 같은 자리에서 같은 값으로 연다.
    """
    now = sent_at.astimezone(KST) if sent_at is not None else datetime.now(KST)
    if store is None:
        store = InMemoryStore(now=now)
    contexts, _ = _domain_contexts(
        child_id=child_id,
        run_id=request_id,
        now=now,
        store=store,
        birth_date=birth_date,
        child_health_granted=child_health_granted,
        location=None,
        activity_weather=None,
        domain_store=domain_store,
        food_catalog=food_catalog,
        food_recipes=food_recipes,
        food_suggestions=food_suggestions,
        food_safety=food_safety,
        activity_safety=activity_safety,
        growth_safety=growth_safety,
        growth_log=growth_log,
        growth_books=growth_books,
        growth_issued_books=growth_issued_books,
        affinity_reader=affinity_reader,
    )
    domain_tasks = tuple(
        DomainTask(request_id, task.agent, task.task_type, task.request_texts) for task in tasks
    )
    return await pipeline.run_suggestions(
        domain_tasks, contexts, save_run_id=save_run_id, save_suggestions=save_suggestions
    )


@dataclass(frozen=True)
class SafetyLabel:
    """api 가 health_safety 행에서 옮겨 담는 칸. agents 는 domains 모델을 받지 않는다."""

    kind: str
    label: str
    status: str


@dataclass(frozen=True)
class AllergenCheck:
    blocked: tuple[str, ...]  # active 에 걸린 항목 — 그 추천은 내보내지 않는다
    unknown: tuple[str, ...]  # 행이 없는 항목 — 채택 전에 묻는다 (unknown_ingredient)


def check_allergens(allergens: Sequence[str], rows: Sequence[SafetyLabel]) -> AllergenCheck:
    """05 의 알레르기 사전검사 (types.ts `Precheck` · #284 규칙). 모델을 부르지 않는다."""
    states = allergen_states(allergens, [(r.kind, r.label, r.status) for r in rows], shared_book())
    return AllergenCheck(
        blocked=tuple(name for name, state in states.items() if state == "active"),
        unknown=tuple(name for name, state in states.items() if state is None),
    )


def allergy_cautions(agent: str, rows: Sequence[SafetyLabel]) -> tuple[str, ...]:
    """그 Agent 가 run 에서 붙이는 알레르기 확인 문구 — 05 가 재생할 때도 같은 말을 한다.

    Activity 만 있다. 꽃가루처럼 식품 사전에 없는 알레르기는 뺄 활동을 코드가 못 정해서 보호자에게
    확인을 맡긴다(`activity/tools/filters.allergy_cautions`). 04 는 Agent 결과의 readout 으로,
    05 는 이 함수로 같은 문구를 낸다 — 05 는 결과 readout 을 저장하지 않아 재생 때 다시 만든다.
    """
    if agent != "activity":
        return ()
    from app.agents.activity.store.ports import SafetyEntry
    from app.agents.activity.tools.filters import allergy_cautions as activity_cautions

    return activity_cautions(
        [SafetyEntry(kind=row.kind, label=row.label, status=row.status) for row in rows]
    )


@dataclass(frozen=True)
class DocCard:
    """일반 추천 근거로 화면에 나가는 문서 행. CC BY 행은 출처 표시가 조건이다."""

    title: str
    source_org: str
    source_title: str
    source_url: str | None


@cache
def _doc_index() -> dict[tuple[str, UUID], DocCard]:
    """검수를 마친(approved) 시드 행. Agent 가 [예시] 로 받은 것과 같은 행이라 같은 id 다."""
    from app.agents.activity.doc_seed import activity_doc_seed
    from app.agents.food.doc_seed import food_doc_seed
    from app.agents.growth.doc_seed import growth_doc_seed

    index: dict[tuple[str, UUID], DocCard] = {}
    seeds = (
        ("activity_doc", activity_doc_seed()),
        ("food_doc", food_doc_seed()),
        ("growth_doc", growth_doc_seed()),
    )
    for kind, entries in seeds:
        for entry in entries:
            meta = entry.meta
            if meta.status != "approved":
                continue
            key = (kind, doc_row_id(kind, meta.doc_key))
            index[key] = DocCard(meta.title, meta.source_org, meta.source_title, meta.source_url)
    return index


def describe_docs(refs: Iterable[tuple[str, UUID]]) -> dict[tuple[str, UUID], DocCard]:
    """근거로 저장된 문서 행 id → 화면에 실을 내용. 시드에 없는(검수가 풀린) 행은 빠진다."""
    index = _doc_index()
    return {ref: index[ref] for ref in refs if ref in index}


def _domain_contexts(
    *,
    child_id: UUID,
    run_id: str,
    now: datetime,
    store: MemoryStore,
    birth_date: date | None,
    child_health_granted: bool | None,
    location: CoarseLocation | None,
    activity_weather: WeatherSource | None,
    domain_store: StoreOpener | None,
    activity_places: PlaceSource | None = None,
    food_catalog: MenuCatalogStore | None = None,
    food_recipes: RecipeCatalogStore | None = None,
    food_suggestions: SuggestionHistoryReader | None = None,
    food_safety: FoodSafetyReader | None = None,
    activity_safety: ActivitySafetyReader | None = None,
    growth_safety: GrowthSafetyReader | None = None,
    growth_log: GrowthLogReader | None = None,
    growth_books: RawBookSearch | None = None,
    growth_issued_books: IssuedBookReader | None = None,
    affinity_reader: AffinityReader | None = None,
) -> tuple[dict[str, DomainContext], RunWrites]:
    """Food · Activity · Growth 컨텍스트. 입력 run 과 05 가 같은 값으로 연다.

    포트가 두 벌이 되지 않게 한 곳에서 만든다.
    """
    # Food 포트는 생일 · 동의만 값으로 연결됐다 (#372). API 가 run 마다 한 번 읽어 넘긴 값을 감싼
    # 포트가 build_gate 의 LifeStage 계산과 동의 확인 자리에 들어가고, 기억 포트는 같은 run 의
    # store 를 읽게 한다. 방금 저장한 관찰이 같은 run 의 추천 근거로 잡혀야 해서다 (루트 §4).
    # 안전은 api 가 넘긴 health_safety 어댑터다(#460). 안 넘긴 호출(테스트 · eval)은
    # UnwiredSafety 라 동의와 상관없이 blocked.safety 로 닫힌다 — "알레르기 0행" 으로 열리면
    # 알레르기가 있는 아이에게도 거르지 않은 안내가 나간다.
    resolved_birth_date = birth_date or (now.date() - _FALLBACK_BIRTH_OFFSET)
    # 관심(profile_affinity)은 Memory store 에 없어 어댑터를 따로 받는다. 시간 경과 archived 는
    # 컨텍스트 today 와 같은 날 기준이다
    today = today_of(now, KST)
    memory_port = (
        StoreFoodMemory(store, affinities=affinity_reader, today=today)
        if domain_store is None
        else StoreFoodMemory(opener=domain_store, affinities=affinity_reader, today=today)
    )
    # Food 가 읽는 공용 데이터 · 추천 이력은 api 가 넘긴 어댑터만 바꿔 끼운다(#383 방식). 문서 행은
    # Activity 처럼 yaml 의 검수된 행이다 — 일반 추천의 근거라 비면 일반 추천을 못 낸다
    food_stores: dict[str, object] = {"docs": InMemoryFoodDocs.from_seed()}
    for name, port in (
        ("catalog", food_catalog),
        ("recipes", food_recipes),
        ("suggestions", food_suggestions),
        ("safety", food_safety),
    ):
        if port is not None:
            food_stores[name] = port
    if child_health_granted is None:
        food_ports = unwired_ports(
            profile=InMemoryProfile({child_id: resolved_birth_date}),
            memory=memory_port,
            **food_stores,
        )
    else:
        child_values = ChildValues(
            child_id=child_id, birth=resolved_birth_date, health_consent=child_health_granted
        )
        food_ports = unwired_ports(
            profile=child_values, consent=child_values, memory=memory_port, **food_stores
        )
    # 쓰기 포트는 호출마다 바로 commit
    writes = RunWrites()
    food_ports = record_food_writes(food_ports, writes)

    food_context = FoodContext(
        child_id=child_id,
        run_id=run_id,
        now=now,
        timezone=KST,
        ports=food_ports,
    )

    # Activity 도 Food 와 같은 자리에서 같은 값을 받는다 (#380). 안전은 api 가 넘긴 어댑터의
    # activity_safety 다(#460) — 안 넘기면 닫힌다. 기억은 같은 run 의 store 를 읽고, 문서 행은
    # yaml 의 approved 행이다.
    activity_child = (
        ChildValues(
            child_id=child_id, birth=resolved_birth_date, health_consent=child_health_granted
        )
        if child_health_granted is not None
        else None
    )
    activity_context = ActivityContext(
        child_id=child_id,
        run_id=run_id,
        now=now,
        timezone=KST,
        ports=activity_unwired_ports(
            profile=activity_child or ActivityProfile({child_id: resolved_birth_date}),
            consent=activity_child or ActivityConsent({child_id: True}),
            memory=(
                StoreActivityMemory(store, affinities=affinity_reader, today=today)
                if domain_store is None
                else StoreActivityMemory(
                    opener=domain_store, affinities=affinity_reader, today=today
                )
            ),
            weather=activity_weather,
            places=activity_places,
            safety=activity_safety,
        ),
        location=location,
    )
    # Growth 도 같은 생일 · 동의 · 기준일 · store 를 받는다. 안전은 api 가 넘긴 어댑터의
    # growth_safety 다 — 교육만 읽고, 동의가 없으면 읽지 않는다. 안 넘기면 UnwiredSafety 라
    # 교육은 동의와 상관없이 blocked.safety 로 닫힌다. 루틴 · 성장 추이는 안전 포트를 읽지 않는다.
    # 도서는 정보나루 · 이미 낸 책 어댑터를 둘 다 받았을 때만 열린다(아니면 closed.book_api).
    # 공지 포트는 없다 — lookup_notice 는 빠진다.
    # 동의 값을 못 받은 호출(테스트 · eval)은 동의 없음으로 읽는다 — 키 · 몸무게를 읽지 않게
    books = (
        LibraryBooks(growth_books)
        if growth_books is not None and growth_issued_books is not None
        else None
    )
    growth_context = GrowthContext(
        child_id=child_id,
        run_id=run_id,
        now=now,
        timezone=KST,
        ports=GrowthPorts(
            profile=activity_child or GrowthProfile({child_id: resolved_birth_date}),
            consent=activity_child or GrowthConsent({child_id: False}),
            safety=growth_safety or GrowthUnwiredSafety(),
            growth_log=growth_log or InMemoryGrowthLog(),
            memory=(
                StoreGrowthMemory(store, affinities=affinity_reader, today=today)
                if domain_store is None
                else StoreGrowthMemory(opener=domain_store, affinities=affinity_reader, today=today)
            ),
            docs=InMemoryGrowthDocs.from_seed(),
            books=books,
            issued_books=growth_issued_books if books is not None else None,
        ),
    )
    return {
        "food": food_context,
        "activity": activity_context,
        "growth": growth_context,
    }, writes
