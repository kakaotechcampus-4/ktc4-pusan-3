"""라이브 테스트. 실제 모델로 발화 하나를 Supervisor → Memory → Food 까지 돌린다.

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/supervisor/test.py -m live -k end_to_end -s   # 끝까지
    uv run pytest tests/eval/agents/supervisor/test.py -m live -k split -s        # Supervisor 단독
    uv run pytest tests/eval/agents/supervisor/test.py -m live -s                 # 둘 다


기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
SUPERVISOR_MODEL · SUPERVISOR_BASE_URL 이 비면 MEMORY_* 를 쓴다 — 기준선이 그 상태다.
후보 모델은 셸 환경변수로 바꿔 끼운다: $env:SUPERVISOR_MODEL / $env:SUPERVISOR_BASE_URL

  split        Supervisor만 호출: 조각을 어떻게 나누고 어디로 보내는지
  end_to_end   Supervisor → Memory(task 모드) → Food까지
               Food 는 테스트 가짜가 아니라 agents/food 의 mock 그대로다 —
               실구현이 들어오면 이 테스트가 그대로 회귀 테스트가 된다

"""

import asyncio
import json
import os
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import ALL_DAY_END, ALL_DAY_START, DateRange
from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FeedingStage, FoodTaskType
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.schemas.task import WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import ObservationRow
from app.agents.memory.tools.observation import MEAL_SLOTS  # 끼니 목록은 tool 이 정본이다
from app.agents.pipeline import MAX_MODEL_CALLS, PipelineResult, handle_input
from app.agents.supervisor import agent as supervisor
from app.agents.supervisor.schemas import DomainAgentName, SegmentKind, normalize
from tests.eval.agents.routing_cases import (
    CASES_BY_ID,
    RC_CASES,
    T_CASES,
    R,
    RoutingCase,
    SplitScore,
    covered_by,
    format_score,
    format_segments,
    format_spans,
    score_split,
)

pytestmark = pytest.mark.live

# 흔들림(같은 입력에 결과가 바뀌는 것)을 보려면 2 이상으로 올린다
TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))

KST = ZoneInfo("Asia/Seoul")
# 기준 시각은 오늘 오전 9시. 날짜를 박아 두면 모델이 보는 "현재 시각" 이 실제와 달라진다.
# 결과를 나란히 비교할 때만 고정한다 — $env:EVAL_NOW="2026-09-09" (test_memory.py 와 같은 변수)
_PINNED = os.getenv("EVAL_NOW")
TODAY = date.fromisoformat(_PINNED) if _PINNED else datetime.now(KST).date()
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=KST)
CHILD = UUID("00000000-0000-7000-8000-000000000001")
WRITER = UUID("00000000-0000-7000-8000-0000000000ff")
DOMAINS = ("food", "health", "education", "activity", "routine")
RESULT_PATH = Path(
    os.getenv("TEST_RESULT_PATH", str(Path(__file__).with_name("test_results.jsonl")))
)
# 있으면 비용을 같이 찍는다. $env:LLM_INPUT_PRICE_PER_M="0.15" 처럼 준다
INPUT_PRICE = float(os.getenv("LLM_INPUT_PRICE_PER_M", "0"))
OUTPUT_PRICE = float(os.getenv("LLM_OUTPUT_PRICE_PER_M", "0"))

_CASES = (*RC_CASES, *T_CASES)


def _client() -> LLMClient:
    try:
        return LLMClient(role="supervisor")
    except LLMConfigError as exc:
        pytest.skip(f"Supervisor 설정이 필요합니다: {exc}")


@pytest.mark.parametrize("case", _CASES, ids=[case.case_id for case in _CASES])
def test_supervisor_split(case: RoutingCase, expect: Callable[..., None]) -> None:
    """조각을 어떻게 나누고 어디로 보내는지 본다. 채점은 구간 단위다.

    Step 5 에서는 **틀린 라벨로 실패시키지 않는다** — 프롬프트를 고칠 근거를 모으는 단계다.
    합격선과 assert 는 Step 9·10 에서 붙인다. 여기서 실패하는 건 Supervisor 가 아예
    결과를 못 낸 경우(검증 실패·LLM 오류)뿐이다.
    """
    # 라벨이 틀려도 실패하지 않는다 — 정답 구간은 점수로만 본다. 그래서 둘을 나눠 적는다
    expect(f"검증을 통과한 출력이 나온다 (라벨은 점수로만 본다) · 정답 구간 {format_spans(case)}")
    client = _client()
    for attempt in range(1, TEST_REPEAT + 1):
        started = time.perf_counter()
        # keep_rejected — 버려진 조각을 콘솔에서 본다. 케이스 문장이 합성이라 괜찮다
        result = asyncio.run(supervisor.run(case.text, client=client, keep_rejected=True))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        score = score_split(case, result.output)

        suffix = f" #{attempt}" if TEST_REPEAT > 1 else ""
        print(f"\n[{case.case_id}{suffix}] {case.watch}")
        print(f"    S {result.model_calls}회 {elapsed_ms}ms  {format_segments(result.output)}")
        print(f"      {format_score(score)}")
        if result.error:
            print(f"      검증 실패: {result.error} ({result.detail})")
            print(f"      버려진 조각: {format_segments(result.rejected)}")
            if result.rejected_raw:  # 스키마에서 걸리면 조각이 객체가 되지 못한다
                print(f"      모델이 낸 원본: {result.rejected_raw}")

        assert result.error is None, (
            f"{case.case_id}: Supervisor 실패 {result.error} ({result.detail})"
        )


# ── 끝까지 (Step 9) ─────────────────────────────────────────────
@dataclass(frozen=True)
class LiveCase:
    case: RoutingCase
    stage: FeedingStage
    label: str  # 변형이면 접미사가 붙는다 (RC20-i)


# 일정의 시간 구간 전용.
# 여기서 보는 건 라우팅 정답이 아니라 "모델이 낸 인자로 어떤 구간이 저장되는가"이다.
_SCHEDULE_CASES: tuple[RoutingCase, ...] = (
    RoutingCase(
        "SC01",
        "금요일부터 일요일까지 캠프 가는데 오전 9시 시작이고 마지막 날 오후 5시에 끝나.",
        (
            R(
                "금요일부터 일요일까지 캠프 가는데 오전 9시 시작이고 마지막 날 오후 5시에 끝나",
                WorkType.SCHEDULE,
            ),
        ),
        watch="여러 날 일정에 ends_on 을 쓰는가 — 안 쓰면 종료가 첫날로 붙는다",
    ),
    RoutingCase(
        "SC02",
        "토요일 밤 11시부터 새벽 1시까지 불꽃놀이 보러 가.",
        (R("토요일 밤 11시부터 새벽 1시까지 불꽃놀이 보러 가", WorkType.SCHEDULE),),
        watch="자정을 넘기는 구간. ends_on 없이 새벽 1시만 주면 규칙이 되묻는다",
    ),
    RoutingCase(
        "SC03",
        "일요일은 하루 종일 마을 축제인데 오후 5시에 끝나.",
        (R("일요일은 하루 종일 마을 축제인데 오후 5시에 끝나", WorkType.SCHEDULE),),
        watch="종일 + 종료 시각은 같이 못 쓴다. 거부를 받고 모델이 어떻게 고치는가",
    ),
)

# 저장된 값 하나만 비우기 (#93). 기록은 남기고 그 값만 clear 로 지우는가
_CLEAR_CASES: tuple[RoutingCase, ...] = (
    RoutingCase(
        "CL01",
        "아까 열 난 기록 있잖아, 시간은 정확하지 않으니까 빼줘.",
        (R("아까 열 난 기록 있잖아, 시간은 정확하지 않으니까 빼줘", WorkType.LOOKUP_EDIT),),
        seed="오늘 오전 8시 발열 observation_health 1건 (body_part 이마)",
        watch='clear=["observed_time"] 을 쓰는가. null 로 보내거나 기록째 지우면 실패',
    ),
    RoutingCase(
        "CL02",
        "모레 운동회 끝나는 시간은 아직 모르니까 빼줘.",
        (R("모레 운동회 끝나는 시간은 아직 모르니까 빼줘", WorkType.LOOKUP_EDIT),),
        seed="모레 15:00~17:00 운동회 event 1건",
        watch='clear=["ends_at"] 을 쓰는가. ends_time 을 clear 에 넣으면 거부받고 고치는가',
    ),
)

_E2E_CASES: tuple[LiveCase, ...] = (
    *(LiveCase(case, case.stage, case.case_id) for case in RC_CASES),
    # 같은 입력을 영아기로 한 번 더 — 식이 단계는 발화가 아니라 아이 나이에서 코드가 정한다
    LiveCase(CASES_BY_ID["RC20"], FeedingStage.INFANT, "RC20-i"),
    # T14 는 test_memory.py 에도 있지만 거기서는 Memory 만 돈다.
    # "저녁에는 뭘 먹이면 좋을까?" 가 Food 로 떨어지는지는 여기서만 실제로 확인된다
    LiveCase(CASES_BY_ID["T14"], CASES_BY_ID["T14"].stage, "T14"),
    *(LiveCase(case, case.stage, case.case_id) for case in _SCHEDULE_CASES),
    *(LiveCase(case, case.stage, case.case_id) for case in _CLEAR_CASES),
)

Seed = Callable[[AgentContext], Awaitable[None]]


async def _seed_lunch(context: AgentContext) -> None:
    """RC13 — 고칠 기록이 있어야 수정 요청이 성립한다."""
    await context.store.create_observation(
        domain="food",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="점심에 떡볶이를 먹었어",
        observed_on=TODAY,
        observed_range=DateRange(start=TODAY, end=TODAY + timedelta(days=1)),
        fields={"subject": "떡볶이", "action": "먹었다"},
    )


_FEVER_AT = datetime(TODAY.year, TODAY.month, TODAY.day, 8, 0, tzinfo=KST)
_SPORTS_DAY = TODAY + timedelta(days=2)
_SPORTS_DAY_START = datetime(_SPORTS_DAY.year, _SPORTS_DAY.month, _SPORTS_DAY.day, 15, tzinfo=KST)


async def _seed_fever(context: AgentContext) -> None:
    """CL01 — 시각까지 적힌 증상 기록. 시각만 비우고 나머지는 남아야 한다."""
    await context.store.create_observation(
        domain="health",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="아침 8시에 이마가 뜨겁고 열이 났어",
        observed_on=TODAY,
        observed_range=DateRange(start=TODAY, end=TODAY + timedelta(days=1)),
        fields={"symptom": ["발열"], "body_part": "이마", "observed_time": _FEVER_AT},
    )


async def _seed_sports_day(context: AgentContext) -> None:
    """CL02 — 끝나는 시각까지 있는 일정. 끝만 비우고 시작은 남아야 한다."""
    await context.store.create_event(
        child_id=CHILD,
        title="운동회",
        starts_at=_SPORTS_DAY_START,
        ends_at=_SPORTS_DAY_START + timedelta(hours=2),
        all_day=False,
        fields={"event_type": "episodic", "category": "institution", "created_by": "agent"},
    )


_SEEDS: dict[str, Seed] = {"RC13": _seed_lunch, "CL01": _seed_fever, "CL02": _seed_sports_day}


@dataclass
class Observed:
    """한 번 돌린 결과. 콘솔·JSONL·판정이 전부 여기서 나온다."""

    live: LiveCase
    result: PipelineResult
    score: SplitScore
    observations: dict[str, list[ObservationRow]]
    events: tuple[EventDraft, ...]  # 제출을 기다리는 일정 초안. 저장된 행이 아니다
    order: list[str]  # 이벤트 종류 순서
    elapsed_ms: int
    failures: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # 저장에 안 남은 관찰 구간 (콘솔 전용)

    @property
    def rows(self) -> list[ObservationRow]:
        return [row for domain in DOMAINS for row in self.observations[domain]]


_RECORDS: list[dict[str, Any]] = []


@pytest.fixture(scope="session", autouse=True)
def _results() -> Iterator[None]:
    RESULT_PATH.unlink(missing_ok=True)
    _RECORDS.clear()
    yield
    if _RECORDS:
        print(_format_summary(_RECORDS))


@pytest.mark.parametrize("live", _E2E_CASES, ids=[live.label for live in _E2E_CASES])
def test_end_to_end(live: LiveCase, expect: Callable[..., None]) -> None:
    """입력 하나를 끝까지 돌리고 조각이 어디로 갔는지 본다."""
    expect(*_expected_end_to_end(live))
    clients = _clients()
    for attempt in range(1, TEST_REPEAT + 1):
        observed = asyncio.run(_run_once(live, *clients))
        record = _record(observed)
        _RECORDS.append(record)
        _print_case(observed, attempt)
        with RESULT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        assert not observed.failures, f"{live.label}: " + " / ".join(observed.failures)


def _expected_end_to_end(live: LiveCase) -> tuple[str, ...]:
    """통과하려면 무엇이 맞아야 하는가. 결과 파일의 [기대] 가 된다 (conftest).

    _judge 가 끊는 것만 적는다. 라벨 점수는 보고만 하므로 여기 넣지 않는다.
    """
    case = live.case
    observe = [
        span.text
        for span in case.spans
        if span.strict and span.kind == SegmentKind.RECORD and span.work == WorkType.OBSERVE
    ]
    food_tasks = {
        span.food_task
        for span in case.spans
        if span.kind == SegmentKind.REQUEST and span.agent == DomainAgentName.FOOD
    }
    expected = [
        f"정답 구간 {format_spans(case)}",
        "Supervisor 출력이 검증을 통과한다 (버려지면 강등 — 요청이 어디에도 못 간다)",
        "입력을 그대로 돌려주지 않는다 — 저장·추천·안내·메모 중 하나는 나온다",
        f"관찰 구간 {len(observe)}개가 저장에 남는다"
        if observe
        else "저장이 필요한 관찰 구간 없음",
        f"Food 호출 {len(food_tasks)}회 (food 요청 조각만 받는다)",
        "저장(Saved)이 Food 호출보다 먼저",
        "Memory 가 health_safety 계열 tool 을 부르지 않는다",
        "food.subject 에 끼니 이름(아침·점심·저녁·간식)이 들어가지 않는다",
        "같은 날 같은 대상이 두 번 저장되지 않는다",
        "저장된 일정의 종료가 시작보다 뒤다 (종일이면 00:00~23:59)",
        "영양소 분석에 propose_meal_candidates 가 안 열리고 filter_food_safety 는 안 보인다",
    ]
    if live.label == "RC20-i":
        expected.append(f"{live.stage} 라 영양소 분석이 unsupported_stage 로 막힌다")
    if case.case_id == "RC15":
        expected.append("보호자 얘기가 아이 관찰로 저장되지 않는다")
    if case.case_id == "RC13":
        expected.append(
            "김밥으로 고쳤다면 말하지 않은 action 은 남는다 (안 고쳤으면 판정 못 함으로 보고)"
        )
    if case.case_id == "CL01":
        expected.append("증상 기록은 1건 그대로, 관찰 시각만 비워지고 나머지 필드는 남는다")
    if case.case_id == "CL02":
        expected.append("일정은 1건 그대로, 끝만 비워지고 시작은 남는다")
    return tuple(expected)


def _clients() -> tuple[LLMClient, LLMClient]:
    try:
        return LLMClient(role="supervisor"), LLMClient(role="memory")
    except LLMConfigError as exc:
        pytest.skip(f"Supervisor·Memory 설정이 필요합니다: {exc}")


async def _run_once(
    live: LiveCase, supervisor_client: LLMClient, memory_client: LLMClient
) -> Observed:
    store = InMemoryStore(now=NOW)
    memory_context = AgentContext(
        child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store
    )
    food_context = FoodContext(
        child_id=CHILD,
        now=NOW,
        timezone=KST,
        stage=live.stage,
        memory=None,  # type: ignore[arg-type]
        profile=None,  # type: ignore[arg-type]
        safety=None,  # type: ignore[arg-type]
        menu=None,  # type: ignore[arg-type]
        nutrition=None,  # type: ignore[arg-type]
    )
    seed = _SEEDS.get(live.case.case_id)
    if seed is not None:
        await seed(memory_context)

    emitted: list[Any] = []
    started = time.perf_counter()
    result = await handle_input(
        live.case.text,
        memory_context,
        food_context,
        run_id=f"live-{live.label}",
        supervisor_client=supervisor_client,
        memory_client=memory_client,
        emit=emitted.append,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    observations = {
        domain: await store.query_observations(domain=domain, child_id=CHILD) for domain in DOMAINS
    }
    # 일정은 저장되지 않으니 memory가 만든 초안을 본다
    events = result.memory.drafts if result.memory else ()
    observed = Observed(
        live=live,
        result=result,
        score=score_split(live.case, result.supervisor.output),
        observations=observations,
        events=events,
        order=[type(event).__name__ for event in emitted],
        elapsed_ms=elapsed_ms,
    )
    _judge(observed)
    return observed


# ── 판정 ────────────────────────────────────────────────────────
def _judge(observed: Observed) -> None:
    """실패로 끊을 것과 보고만 할 것을 나눈다. 메시지에는 원문을 담지 않는다 (S10)."""
    result, case = observed.result, observed.live.case
    output = result.supervisor.output
    segments = list(output.segments) if output else []

    # 출력이 버려지면 조각이 하나도 안 나눠진다. 기록은 Memory 단독으로 남지만(S2)
    # 요청은 어느 Agent 에도 못 간다 — RC20 이 조용히 통과하던 구멍이다
    # 저장도 추천도 안내도 메모도 없으면 pipeline 이 입력을 그대로 돌려준다.
    # 되물어야 하는 입력에서 Memory 가 빈 응답을 내면 여기로 떨어진다 (RC15)
    if result.failed is not None:
        observed.failures.append(f"입력을 그대로 돌려줬다 ({result.failed.reason})")

    if result.supervisor.error:
        detail = f"{result.supervisor.error} {result.supervisor.detail}"
        observed.failures.append(f"Supervisor 출력이 버려졌다 → 강등 ({detail})")

    # Food 는 food REQUEST 조각만 받는다 (S10)
    allowed = {
        normalize(segment.text)
        for segment in segments
        if segment.kind == SegmentKind.REQUEST and segment.agent == DomainAgentName.FOOD
    }
    stray = sum(
        1
        for task in result.routing.food_tasks
        for text in task.request_texts
        if normalize(text) not in allowed
    )
    if stray:
        observed.failures.append(f"Food 가 food 요청이 아닌 조각을 받았다 ({stray}건)")

    expected = _expected_food_tasks(observed)
    if len(result.food) != expected:
        observed.failures.append(f"Food 호출 {len(result.food)}회 ≠ food 유형 {expected}개")

    for food in result.food:
        # S3 · S6 — 분석에 식품 제안이 열리면 안 되고, 안전 필터는 모델에게 보이면 안 된다
        if (
            food.task_type == FoodTaskType.NUTRIENT_ANALYSIS
            and "propose_meal_candidates" in food.tools
        ):
            observed.failures.append("영양소 분석에 propose_meal_candidates 가 열렸다")
        if "filter_food_safety" in food.tools:
            observed.failures.append("filter_food_safety 가 모델에게 보인다")

    if observed.live.label == "RC20-i":
        status = result.food[0].status if result.food else "(호출 없음)"
        if status != "unsupported_stage":
            observed.failures.append(f"영아기 영양소 분석이 unsupported_stage 가 아니다: {status}")

    # 루트 §4 — 저장이 검색보다 먼저
    if "FoodRouted" in observed.order and "Saved" in observed.order:
        if observed.order.index("FoodRouted") < observed.order.index("Saved"):
            observed.failures.append("Food 가 저장보다 먼저 불렸다")

    memory = result.memory
    if memory is not None:
        leaked = [name for name in memory.tool_names if "safety" in name or "allerg" in name]
        if leaked:  # 루트 §2 — 알레르기·건강 정보는 LLM 이 만들거나 고치지 않는다
            observed.failures.append(
                f"Memory 가 health_safety 계열 tool 을 불렀다 ({len(leaked)}건)"
            )

    # 같은 날 같은 대상이 두 행이면 중복이다 (라이브 RC08 — 킥보드 2행)
    seen = Counter(
        (domain, str(row.fields.get("subject")), row.observed_on)
        for domain in DOMAINS
        for row in observed.observations[domain]
        if row.fields.get("subject")
    )
    twice = [key for key, count in seen.items() if count > 1]
    if twice:
        observed.failures.append(f"같은 관찰이 두 번 저장됐다 ({len(twice)}건)")

    # subject 는 병합·검색 키다. 끼니 이름이 들어가면
    # profile_affinity 에 "저녁을 좋아한다" 가 쌓인다
    slots = [
        row.fields["subject"]
        for row in observed.observations["food"]
        if str(row.fields.get("subject", "")).strip() in MEAL_SLOTS
    ]
    if slots:
        observed.failures.append(f"food.subject 에 끼니 이름이 들어갔다 ({len(slots)}건)")

    _judge_event_intervals(observed)

    if case.case_id == "RC15":  # 루트 §2 — 부모의 말은 아이의 fact 가 아니다
        if [row for row in observed.rows if "피곤" in row.raw_text]:
            observed.failures.append("보호자 얘기가 아이 관찰로 저장됐다")

    _judge_clear(observed)

    # fail-open — Supervisor 가 어떻게 나눴든 관찰은 저장에 남아야 한다 (S2)
    saved_texts = [row.raw_text for row in observed.rows]
    observed.missing = [
        span.text
        for span in case.spans
        if span.strict
        and span.kind == SegmentKind.RECORD
        and span.work == WorkType.OBSERVE
        and not covered_by(case, span, saved_texts)
    ]
    if observed.missing:
        observed.failures.append(f"관찰 구간 {len(observed.missing)}개가 저장에 없다")

    _report(observed)


def _judge_clear(observed: Observed) -> None:
    """비우기(#93). 요청한 값만 비워지고 기록과 나머지 값은 남는가."""
    case_id = observed.live.case.case_id

    if case_id == "RC13":  # 고치기만 한 요청에서 말하지 않은 값을 지우지 않는다
        food = observed.observations["food"]
        if not any(row.fields.get("subject") == "김밥" for row in food):
            # 고치지 않았으면 action 이 남아 있어도 clear 를 참았는지는 알 수 없다
            observed.reports.append("수정이 안 일어나 clear 과사용은 판정 못 함")
        elif not any(row.fields.get("action") == "먹었다" for row in food):
            observed.failures.append("수정 요청에서 말하지 않은 action 이 지워졌다")

    if case_id == "CL01":
        rows = observed.observations["health"]
        if len(rows) != 1:
            observed.failures.append(f"증상 기록이 1건이 아니다 ({len(rows)}건)")
            return
        fields = rows[0].fields
        if fields.get("observed_time") is not None:
            observed.failures.append("관찰 시각이 지워지지 않았다")
        if fields.get("symptom") != ["발열"] or fields.get("body_part") != "이마":
            observed.failures.append("말하지 않은 필드가 바뀌었다")

    if case_id == "CL02":
        if len(observed.events) != 1:
            observed.failures.append(f"일정 초안이 1건이 아니다 ({len(observed.events)}건)")
            return
        draft = observed.events[0]
        if draft.ends_at is not None:
            observed.failures.append(f"초안의 끝이 지워지지 않았다 ({_interval(draft)})")
        if draft.starts_at != _SPORTS_DAY_START:
            observed.failures.append(f"초안의 시작이 바뀌었다 ({_interval(draft)})")


def _judge_event_intervals(observed: Observed) -> None:
    """초안의 시간 구간이 성립하는지 확인한다. 보호자에게 올라가기 전 마지막 관문이다."""
    for row in observed.events:
        start = row.starts_at.astimezone(KST)
        end = row.ends_at.astimezone(KST) if row.ends_at else None

        if end is not None and end <= start:
            observed.failures.append(f"일정의 종료가 시작보다 앞서거나 같다 ({_interval(row)})")
        if row.all_day and (
            start.time() != ALL_DAY_START or end is None or end.time() != ALL_DAY_END
        ):
            # 종일 일정은 00:00~23:59
            observed.failures.append(f"종일 일정이 00:00~23:59가 아니다 ({_interval(row)})")


def _interval(row: EventDraft) -> str:
    """초안의 시간 구간 한 줄. 하루 안에 끝나면 끝 날짜를 생략한다."""
    start = row.starts_at.astimezone(KST)
    end = row.ends_at.astimezone(KST) if row.ends_at else None
    if row.all_day and end is not None:
        if (start.time(), end.time()) == (ALL_DAY_START, ALL_DAY_END):
            # 00:00~23:59 == 종일이라 시각을 적지 않는다
            # 어긋난 종일 일정은 아래로 내려가 실제 시각을 그대로 보여준다
            if end.date() == start.date():
                return f"종일 {start:%m/%d}"
            return f"종일 {start:%m/%d}~{end:%m/%d}"
    if end is None:
        return f"{start:%m/%d %H:%M}~"
    if end.date() == start.date():
        return f"{start:%m/%d %H:%M}~{end:%H:%M}"
    return f"{start:%m/%d %H:%M}~{end:%m/%d %H:%M}"


def _report(observed: Observed) -> None:
    """실패로 끊지는 않지만 Step 10 에서 봐야 하는 것들."""
    result, score = observed.result, observed.score
    budget = MAX_MODEL_CALLS + (1 if result.rerouted else 0)  # pipeline._log 와 같은 기준
    if result.model_calls > budget:
        observed.reports.append(f"호출 예산 초과 model_calls={result.model_calls}/{budget}")
    if result.rerouted:  # 처음 나눈 결과가 틀렸다는 뜻이다 — 고쳐졌어도 Supervisor 오분류로 센다
        observed.reports.append(f"재분기 {result.rerouted.bounced}")
    if "Unwritten" in observed.order:  # 기록 조각을 짚었는데 쓰기가 없었다 (RC01 — 말만 한 run)
        observed.reports.append("쓰기 없음")
    if score.record_to_request:
        observed.reports.append(f"RECORD→REQUEST {score.record_to_request}")
    if score.request_to_record:
        observed.reports.append(f"REQUEST→RECORD {score.request_to_record}")
    if score.record_found > score.work_ok:
        observed.reports.append(f"작업 종류 불일치 {score.record_found - score.work_ok}")
    if score.lookup_edit_missed:
        observed.reports.append(f"lookup_edit 누락 {score.lookup_edit_missed}")
    if score.rec_to_analysis or score.analysis_to_rec:
        observed.reports.append(
            f"food 유형 추천→분석 {score.rec_to_analysis} · 분석→추천 {score.analysis_to_rec}"
        )
    if score.missed:
        observed.reports.append(f"못 찾은 구간 {score.missed}")
    if result.failed is not None:
        observed.reports.append(f"failed={result.failed.reason}")
    if observed.live.case.case_id == "RC24":
        # Step 1 규칙의 첫 측정 — 매일 반복되는 식사 일과는 core 일정이어야 한다
        types = [str(draft.event_type) for draft in observed.events]
        observed.reports.append(f"RC24 event_type={types or '(일정 없음)'}")
    if observed.live.case.case_id.startswith("SC"):
        # 저장된 구간을 그대로 남긴다 — 모델이 ends_on을 썼는지 확인 가능하다.
        # 일정이 없으면 규칙이 되묻고 모델이 고치지 못한 것이다
        stored = " · ".join(_interval(draft) for draft in observed.events)
        observed.reports.append(f"{observed.live.case.case_id} 구간={stored or '(일정 없음)'}")


def _expected_food_tasks(observed: Observed) -> int:
    """Supervisor 출력이 낸 food 유형 수. agent 상한에 걸려 food 가 빠지면 0 이다."""
    if "food" in observed.result.routing.dropped_agents:
        return 0
    output = observed.result.supervisor.output
    if output is None:
        return 0
    return len(
        {
            segment.food_task
            for segment in output.segments
            if segment.kind == SegmentKind.REQUEST and segment.agent == DomainAgentName.FOOD
        }
    )


# ── 출력 ────────────────────────────────────────────────────────
def _print_case(observed: Observed, attempt: int) -> None:
    result = observed.result
    suffix = f" #{attempt}" if TEST_REPEAT > 1 else ""
    print(f"\n[{observed.live.label}{suffix}] {observed.live.case.watch}")
    print(
        f"    S {result.supervisor.model_calls}회 {result.supervisor.latency_ms}ms  "
        f"{format_segments(result.supervisor.output)}"
    )
    if result.supervisor.error:
        print(f"      검증 실패: {result.supervisor.error} ({result.supervisor.detail}) → 강등")
    if result.rerouted:
        print(
            f"      다시 나눔: Memory 가 적지 않은 조각 {result.rerouted.bounced}개 → "
            f"Food {list(result.rerouted.food_tasks)} (위 조각은 다시 나눈 결과)"
        )

    memory = result.memory
    if memory is None:
        print("    M 호출 못 함")
    else:
        note = f" → 메모 {memory.final_message!r}" if memory.final_message else ""
        print(
            f"    M {memory.steps}회  {_tool_summary(memory.tool_names)}  "
            f"ended_by={memory.ended_by}{note}"
        )
        print(f"      저장 {_saved_summary(observed)}")
    for food in result.food:
        safety = "health_safety 사전 확인 대상" if food.requires_safety_check else "사전 확인 없음"
        print(
            f"    F {food.task_type}·{food.stage} ← {list(food.request_texts)} · "
            f"tools {len(food.tools)} · {safety} · {food.status}"
        )
    if result.routing.unavailable_agents:
        print(f"      미구현 agent: {list(result.routing.unavailable_agents)}")
    for guidance in result.routing.guidance:
        print(f"      안내: {guidance.code}")
    if result.failed is not None:
        print(f"      실패: {result.failed.reason}")
    print(
        f"    model_calls={result.model_calls} {observed.elapsed_ms}ms  "
        f"{format_score(observed.score)}"
    )
    disagreement = observed.result.disagreement
    print(
        f"      불일치 Memory만 {list(disagreement.memory_only)} · "
        f"Supervisor만 {list(disagreement.supervisor_only)}"
    )
    if observed.missing:
        print(f"      ⚠ 저장에 없는 관찰 구간: {observed.missing}")
    for report in observed.reports:
        print(f"      · {report}")


def _tool_summary(names: list[str]) -> str:
    if not names:
        return "tool 호출 없음"
    counts = Counter(names)
    return " · ".join(name if count == 1 else f"{name} ×{count}" for name, count in counts.items())


def _saved_summary(observed: Observed) -> str:
    parts = [f"{domain} {len(rows)}" for domain, rows in observed.observations.items() if rows]
    for draft in observed.events:
        parts.append(
            f"초안 {draft.title!r}({draft.event_type}, {_interval(draft)}, "
            f"준비물 {len(draft.items)})"
        )
    return " · ".join(parts) or "없음"


def _record(observed: Observed) -> dict[str, Any]:
    """JSONL 한 줄. 조각·메모 원문은 넣지 않는다 (S10)."""
    result = observed.result
    memory = result.memory
    usage = _usage(observed)
    return {
        "case_id": observed.live.label,
        "stage": str(observed.live.stage),
        "supervisor": "env",  # 모델명 대신 (plan.md D7)
        "supervisor_error": result.supervisor.error,
        "supervisor_detail": result.supervisor.detail,
        "supervisor_calls": result.supervisor.model_calls,
        "supervisor_latency_ms": result.supervisor.latency_ms,
        "degraded": result.routing.degraded,
        "intent": result.routing.intent_type,
        "score": asdict(observed.score),
        "memory_steps": memory.steps if memory else None,
        "memory_ended_by": memory.ended_by if memory else None,
        "memory_tools": memory.tool_names if memory else [],
        "saved": {domain: len(rows) for domain, rows in observed.observations.items()},
        "event_drafts": [
            {
                "op": draft.op,
                "event_type": str(draft.event_type),
                "items": len(draft.items),
                "changed": list(draft.changed),
                # 구간까지 남겨서 실행마다 다른 시각이 나오는지 결과에서 확인
                "all_day": draft.all_day,
                "starts_at": draft.starts_at.astimezone(KST).isoformat(timespec="minutes"),
                "ends_at": (
                    draft.ends_at.astimezone(KST).isoformat(timespec="minutes")
                    if draft.ends_at
                    else None
                ),
            }
            for draft in observed.events
        ],
        "food": [
            {
                "task_type": str(food.task_type),
                "stage": str(food.stage),
                "status": food.status,
                "tools": len(food.tools),
                "requires_safety_check": food.requires_safety_check,
            }
            for food in result.food
        ],
        "guidance": [guidance.code for guidance in result.routing.guidance],
        "unavailable": list(result.routing.unavailable_agents),
        "dropped": list(result.routing.dropped_agents),
        "failed": result.failed.reason if result.failed else None,
        # 처음 나눈 결과가 조각을 흘려 다시 나눈 수. score·supervisor_* 는 다시 나눈 쪽 기준이다
        "rerouted": result.rerouted.bounced if result.rerouted else 0,
        "disagreement": asdict(result.disagreement),
        "missing_observations": len(observed.missing),
        "model_calls": result.model_calls,
        "latency_ms": observed.elapsed_ms,
        "usage": usage,
        "failures": observed.failures,
        "reports": observed.reports,
    }


def _usage(observed: Observed) -> dict[str, int]:
    total: dict[str, int] = {}
    sources = [observed.result.supervisor.usage]
    if observed.result.memory is not None:
        sources.append(observed.result.memory.usage)
    for source in sources:
        for key, value in source.items():
            total[key] = total.get(key, 0) + value
    return total


# ── 요약 ────────────────────────────────────────────────────────
def _format_summary(records: list[dict[str, Any]]) -> str:
    def total(key: str) -> int:
        return sum(record["score"][key] for record in records)

    def ratio(found: int, spans: int) -> str:
        return f"{found}/{spans} ({found / spans:.0%})" if spans else "해당 없음"

    calls = [record["model_calls"] for record in records]
    latency = [record["latency_ms"] for record in records]
    supervisor_latency = [record["supervisor_latency_ms"] for record in records]
    prompt = sum(record["usage"].get("prompt_tokens", 0) for record in records)
    completion = sum(record["usage"].get("completion_tokens", 0) for record in records)
    not_substring = sum(1 for record in records if record["supervisor_error"] == "NOT_SUBSTRING")

    cases = len({record["case_id"] for record in records})
    within = sum(1 for call in calls if call <= MAX_MODEL_CALLS) / len(calls)
    lines = [
        "",
        f"══ 라이브 요약 — run {len(records)}회 / 케이스 {cases}개",
        f"  RECORD 구간 recall   {ratio(total('record_found'), total('record_spans'))}"
        f"   · RECORD→REQUEST {total('record_to_request')}"
        f" · REQUEST→RECORD {total('request_to_record')}",
        f"  작업 종류            {ratio(total('work_ok'), total('record_spans'))}"
        f"   · lookup_edit 누락 {total('lookup_edit_missed')}",
        f"  agent               {ratio(total('agent_ok'), total('request_spans'))}",
        f"  food 유형            {ratio(total('food_task_ok'), total('food_spans'))}"
        f"   · 추천→분석 {total('rec_to_analysis')} · 분석→추천 {total('analysis_to_rec')}",
        f"  GUARDED             {ratio(total('guarded_found'), total('guarded_spans'))}",
        f"  부분 문자열 위반      {not_substring}/{len(records)}"
        f"   · 강등 {sum(1 for r in records if r['degraded'])}회"
        f" · 재분기 {sum(1 for r in records if r['rerouted'])}회",
        f"  저장 누락(fail-open) {sum(r['missing_observations'] for r in records)}건"
        f"   · failed {sum(1 for r in records if r['failed'])}건",
        f"  호출 수              평균 {sum(calls) / len(calls):.1f} · 최대 {max(calls)}"
        f" · ≤{MAX_MODEL_CALLS} 비율 {within:.0%}",
        f"  지연                 Supervisor p95 {_p95(supervisor_latency)}ms"
        f" · 전체 p95 {_p95(latency)}ms",
        f"  토큰                 prompt {prompt} · completion {completion}"
        f"{_cost(prompt, completion)}",
    ]
    shaky = _shaky(records)
    if TEST_REPEAT > 1:
        lines.append(f"  흔들림               {len(shaky)}개 케이스 {shaky}")
    return "\n".join(lines)


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def _cost(prompt: int, completion: int) -> str:
    if not (INPUT_PRICE or OUTPUT_PRICE):
        return ""
    cost = prompt / 1_000_000 * INPUT_PRICE + completion / 1_000_000 * OUTPUT_PRICE
    return f" · 비용 {cost:.4f}"


def _shaky(records: list[dict[str, Any]]) -> list[str]:
    """같은 케이스를 여러 번 돌렸을 때 결과가 바뀐 케이스. 한 번의 만점은 흔들림을 숨긴다."""
    signatures: dict[str, set[str]] = {}
    for record in records:
        score = record["score"]
        signature = json.dumps(
            [
                score["segments"],
                score["record_found"],
                score["work_ok"],
                score["agent_ok"],
                score["food_task_ok"],
                record["food"],
                record["failed"],
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        signatures.setdefault(record["case_id"], set()).add(signature)
    return sorted(case_id for case_id, seen in signatures.items() if len(seen) > 1)
