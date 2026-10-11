"""이어받기 라이브 eval. 답에 섞인 다른 말을 Memory 가 조용히 버리지 않는지 본다 (PR #175).
답이 계속 모자라도 재질문 상한 안에서 끝나는지도 본다 (#246).
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/memory/test_continuation.py -m live -s
    uv run pytest tests/eval/agents/memory/test_continuation.py -m live -k 상한 -s -rs

상한 쪽은 run 을 한 번만 잇고 세 테스트가 나눠 본다. 모델이 상한 전에 그만 물으면 상한 표시를
보는 테스트는 skip 된다. 상한을 확인하지 못한 실행이라는 뜻이고, -rs 가 그 사유를 보여 준다.

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient
from app.agents.memory.agent import MAX_QUESTIONS, MemoryAgentResult, run
from app.agents.memory.bundles import MUTATING_PREFIXES
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import ObservationRow

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
WRITER = UUID(int=2)
DOMAINS = ("food", "health", "education", "activity", "routine")

TODAY = datetime.now(KST).date()
NOW = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=KST)
TWO_DAYS_AGO = TODAY - timedelta(days=2)  # "그저께"
THREE_DAYS_AGO = TODAY - timedelta(days=3)  # "3일 전"
# "다음 주 수요일" 은 이번 주 월요일에서 센다 (test_memory._weekday 와 같은 규칙)
NEXT_WEDNESDAY = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=9)

Rows = dict[str, list[ObservationRow]]
# 답이 기록에 실제로 들어갔는지 본다. 행 수만 보면 조각만 저장하고 답까지 leftover 로
# 버린 경우를 못 가른다
Check = tuple[str, Callable[[Rows, MemoryAgentResult], bool]]


def _observed_on(domain: str, day: date) -> Check:
    return (
        f"{domain} 관찰 날짜가 답에서 온 {day.isoformat()} 이다",
        lambda rows, _: any(row.observed_on == day for row in rows[domain]),
    )


def _field_has(domain: str, needle: str) -> Check:
    return (
        f"{domain} 관찰에 답의 '{needle}' 이 들어갔다",
        lambda rows, _: any(
            # fields 에는 DateRange 같은 객체도 들어 있다
            needle in json.dumps(row.fields, ensure_ascii=False, default=str)
            for row in rows[domain]
        ),
    )


def _draft_at(day: date, hour: int) -> Check:
    want = datetime(day.year, day.month, day.day, hour, tzinfo=KST)
    return (
        f"일정 초안이 답의 {want.isoformat()} 에 잡혔다",
        lambda _, result: len(result.drafts) == 1 and result.drafts[0].starts_at == want,
    )


_COUGH = PendingMemoryContext("요즘 기침해", "기침은 언제부터였어요?", WorkType.OBSERVE)
_LUNCH = PendingMemoryContext("점심 먹었어", "점심에 무엇을 먹었어요?", WorkType.OBSERVE)
_CLINIC = PendingMemoryContext(
    "다음 주 수요일 병원 예약 있어", "병원 예약은 몇 시예요?", WorkType.SCHEDULE
)
# Supervisor 가 실패하면 원문 전체가 한 조각이 된다. 조각 안의 두 기록은 섞인 말이 아니다
_WHOLE = PendingMemoryContext(
    "모래놀이하고 뭐 좀 먹었어", "무엇을 먹었어요?", WorkType.OBSERVE, whole=True
)


@dataclass(frozen=True)
class Case:
    case_id: str
    pending: PendingMemoryContext
    answer: str
    rows: dict[str, int]  # 섞인 말을 빼고 끝난 뒤 도메인별 관찰 수. 적지 않은 도메인은 0
    leftover: bool  # 답에 조각과 상관없는 말이 섞였는지
    kind: str | None = None  # 기대하는 reply.kind. None 이면 보지 않는다
    checks: tuple[Check, ...] = ()  # 답이 반영됐는지
    drafts: int = 0  # 섞인 말을 빼고 끝난 뒤 일정 초안 수
    mixed: str = ""  # 섞인 말을 가리키는 낱말. 모델이 그것을 되물었는지 볼 때 쓴다


_COUGH_ANSWERED = (_observed_on("health", TWO_DAYS_AGO),)

CASES = [
    Case("C01", _COUGH, "그저께부터", {"health": 1}, False, checks=_COUGH_ANSWERED),
    Case(
        "C02",
        _COUGH,
        "그저께부터. 그리고 오늘 수영장 다녀왔어",
        {"health": 1},
        True,
        checks=_COUGH_ANSWERED,
        mixed="수영장",
    ),
    Case(
        "C03",
        _COUGH,
        "그저께부터. 저녁 뭐 먹일까?",
        {"health": 1},
        True,
        checks=_COUGH_ANSWERED,
        mixed="저녁",
    ),
    Case("C04", _COUGH, "저녁 뭐 먹일까?", {}, True, kind="question", mixed="저녁"),
    # 모델이 tool 없이 "기록했어요" 라고만 답한 적이 있다(19번 중 1번). 그때도 실패로 막는다
    Case(
        "C05",
        _WHOLE,
        "떡볶이",
        {"activity": 1, "food": 1},
        False,
        checks=(_field_has("food", "떡볶이"),),
    ),
    # 답이 뒤에 와도 답은 반영한다
    Case(
        "C06",
        _COUGH,
        "오늘 수영장 다녀왔어. 기침은 그저께부터야",
        {"health": 1},
        True,
        checks=_COUGH_ANSWERED,
        mixed="수영장",
    ),
    # 같은 기침에 대한 설명은 섞인 말이 아니다. 따로 보내라고 하면 보호자가 쓴 말을 두 번 쓴다
    Case("C07", _COUGH, "그저께부터. 밤에 더 심해", {"health": 1}, False, checks=_COUGH_ANSWERED),
    # 다른 도메인 조각에서도 같다. 섞인 일정은 초안도 만들지 않는다
    Case(
        "C08",
        _LUNCH,
        "김밥. 그리고 내일 소풍 있어",
        {"food": 1},
        True,
        checks=(_field_has("food", "김밥"),),
        mixed="소풍",
    ),
    # 일정 조각의 답은 초안 시각으로 들어가고, 섞인 기록은 저장하지 않는다
    Case(
        "C09",
        _CLINIC,
        "오전 10시. 그리고 오늘 수영장 다녀왔어",
        {},
        True,
        checks=(_draft_at(NEXT_WEDNESDAY, 10),),
        drafts=1,
        mixed="수영장",
    ),
    # 숫자로 센 날짜. 날짜 규칙이 "3일 전" 을 못 읽던 때는 DATE_UNPARSEABLE 로 다시 물었다
    Case(
        "C10",
        _COUGH,
        "3일 전부터",
        {"health": 1},
        False,
        checks=(_observed_on("health", THREE_DAYS_AGO),),
    ),
    # 원문 전체 조각은 "조각 안의 기록을 모두 저장한다" 를 받는다.
    # 그 말이 답에 섞인 수영장까지 번지는지 확인한다
    Case(
        "C11",
        _WHOLE,
        "떡볶이. 그리고 오늘 수영장 다녀왔어",
        {"activity": 1, "food": 1},
        True,
        checks=(_field_has("food", "떡볶이"), _field_has("activity", "모래")),
        mixed="수영장",
    ),
]


def _client() -> LLMClient:
    settings = AgentSettings()
    if not settings.MEMORY_API_KEY or not settings.MEMORY_BASE_URL:
        pytest.skip("MEMORY_API_KEY / MEMORY_BASE_URL 이 필요합니다. apps/api/.env 를 확인하세요.")
    return LLMClient(settings, role="memory")


async def _run(case: Case, client: LLMClient) -> tuple[MemoryAgentResult, Rows]:
    store = InMemoryStore(now=NOW)
    context = AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)
    result = await run(case.answer, context, client=client, continuation=case.pending)
    rows = {
        domain: await store.query_observations(domain=domain, child_id=CHILD) for domain in DOMAINS
    }
    return result, rows


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_이어받기_답에_섞인_말(case: Case) -> None:
    result, rows = asyncio.run(_run(case, _client()))
    counts = {domain: len(rows[domain]) for domain in DOMAINS}
    expected = {domain: case.rows.get(domain, 0) for domain in DOMAINS}
    print(
        f"\n[{case.case_id}] rows={counts} drafts={len(result.drafts)} "
        f"leftover={result.leftover} kind={result.reply.kind if result.reply else None} "
        f"pending={result.pending is not None} tools={result.tool_names} "
        f"{result.final_message!r}"
    )

    # 어느 케이스든 지킨다. 답까지 leftover 로 버리지 않았는지는 checks 가 본다
    for label, check in case.checks:
        assert check(rows, result), label
    # 이어받기는 조기 종료하지 않아 늘 말로 끝난다. 빈 text 는 화면에 안내 문장만 남긴다
    assert result.reply is not None and result.reply.text.strip()
    if case.kind is not None:
        assert result.reply.kind == case.kind
        assert result.reply.text != case.answer  # 보호자의 말을 질문으로 되돌려 주지 않는다
    if result.leftover:
        # 따로 보내라고 해 놓고 섞인 것을 저장했으면 보호자가 다시 보내 두 번 저장된다
        assert all(counts[domain] <= expected[domain] for domain in DOMAINS), "섞인 것을 저장"
        assert len(result.drafts) <= case.drafts, "섞인 일정으로 초안을 만듦"

    if not case.leftover:
        # 섞인 말이 없으면 따로 보내라고 하지 않는다. 하면 보호자가 쓴 말을 두 번 쓴다
        assert counts == expected
        assert len(result.drafts) == case.drafts
        assert result.leftover is False, "섞인 말이 없는데 따로 보내라고 함"
        return

    # 섞인 말은 둘 중 하나로 끝나면 된다.
    #   남긴다: 처리하지 않고 leftover 로 알린다. 코드가 "따로 보내 주세요" 를 붙인다
    #   처리한다: 저장하거나 초안을 만든다. 04 결과에 서서 보호자가 보고, 안내가 안 붙어
    #            다시 보내지도 않는다. 프롬프트로 막으면 아래의 조용한 누락이 늘 수 있다
    # 맥락 없이 되물은 것도 처리한 쪽이다. pipeline 이 message 로 내리고 따로 보내라고 한다
    assert all(counts[domain] >= expected[domain] for domain in DOMAINS), "조각의 기록이 빠짐"
    stored = (
        any(counts[domain] > expected[domain] for domain in DOMAINS)
        or len(result.drafts) > case.drafts
    )
    asked = (
        result.reply.kind == "question"
        and result.pending is None
        and case.mixed in result.reply.text
    )
    assert result.leftover or stored or asked, "섞인 말을 처리도 안내도 하지 않음"


# ── 재질문 상한 (#246) ──────────────────────────────────────────────────
# 답이 계속 모자라면 다시 묻는다. 처음 질문(_COUGH) 뒤로 답할 기회는 상한만큼이고, 그 안에서
# 맥락이 끝나야 한다. run 은 한 번만 잇고 세 테스트가 나눠 본다. 끝나는지는 늘 보고, 상한 표시는
# 모델이 상한에서 또 물은 실행에서만 본다(그 전에 그만 물으면 skip). 말로 끝났으면 저장하지 않고
# 저장했다고 하지 않는지도 본다
_VAGUE_ANSWERS = ("잘 모르겠어요", "기억이 잘 안 나요", "글쎄요", "모르겠어요")
# 저장했다는 말. 말투가 여러 가지라 다 잡지는 못한다. "기록하지 못했어요" 는 걸리지 않고,
# "알려 주시면 기록할게요" 처럼 나중 일을 말하는 문장은 _LATER 로 뺀다
_SAVE_CLAIM = re.compile(
    r"(기록|저장)(할게|했|해 ?(두|둘|뒀|놓|놨|드릴))|남겨 ?(둘|두었|뒀|놓|놨)|것으로 (해 ?)?둘게"
)
_LATER = re.compile(r"면\b|그때|나중에|다음에")


def _wrote(result: MemoryAgentResult) -> bool:
    """그 run 에서 성공한 쓰기가 있는가. 실패한 쓰기는 저장이 아니다."""
    return any(call.success and call.name.startswith(MUTATING_PREFIXES) for call in result.calls)


def _claims_save(text: str) -> bool:
    """지금 저장한 것처럼 말하는 문장이 있는가."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return any(_SAVE_CLAIM.search(s) and not _LATER.search(s) for s in sentences)


class _Warnings(logging.Handler):
    """Memory 가 남긴 경고. 이어받기에서는 스키마 불일치 · 반복 상한뿐이라 있으면 비정상 종료다."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@dataclass(frozen=True)
class Chain:
    results: list[MemoryAgentResult]  # 답 하나에 run 하나. 맥락이 끝나면 멈춘다
    warnings: list[str]

    @property
    def last(self) -> MemoryAgentResult:
        return self.results[-1]

    @property
    def asked_at_limit(self) -> bool:
        """상한만큼 답한 뒤에도 모델이 또 물었는가. 상한 표시는 이 실행에서만 볼 수 있다."""
        reply = self.last.reply
        return len(self.results) == MAX_QUESTIONS and reply is not None and reply.kind == "question"


async def _run_until_settled(client: LLMClient) -> list[MemoryAgentResult]:
    store = InMemoryStore(now=NOW)
    context = AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)
    pending: PendingMemoryContext | None = _COUGH
    results: list[MemoryAgentResult] = []
    for answer in _VAGUE_ANSWERS:
        if pending is None:
            break
        result = await run(answer, context, client=client, continuation=pending)
        results.append(result)
        pending = result.pending
    return results


@pytest.fixture(scope="module")
def vague_chain() -> Chain:
    client = _client()
    # 스키마 불일치는 message 로 끝나서 결과만으로는 모델이 말로 끝낸 것과 가를 수 없다
    memory_logger = logging.getLogger(run.__module__)
    warnings = _Warnings()
    memory_logger.addHandler(warnings)
    try:
        results = asyncio.run(_run_until_settled(client))
    finally:
        memory_logger.removeHandler(warnings)
    for n, result in enumerate(results, start=1):
        print(
            f"\n[상한 {n}] kind={result.reply.kind if result.reply else None} "
            f"pending={result.pending is not None} limit={result.ask_limit_reached} "
            f"tools={result.tool_names} {result.final_message!r}"
        )
    return Chain(results, warnings.messages)


def test_답이_계속_모자라면_재질문_상한_안에서_끝난다(vague_chain: Chain) -> None:
    assert len(_VAGUE_ANSWERS) == MAX_QUESTIONS, "답이 상한보다 적으면 상한까지 이어 볼 수 없음"
    # 반복 상한 · 스키마 불일치로 끝난 run 도 맥락이 없다. 상한 안에서 끝난 게 아니라 망가진 것이다
    assert not vague_chain.warnings, f"Memory 가 비정상으로 끝남: {vague_chain.warnings}"
    for n, result in enumerate(vague_chain.results, start=1):
        assert result.completed, f"{n}번째 run 이 반복 상한에 걸림"
        assert result.reply is not None, f"{n}번째 run 이 말 없이 끝남"
        assert result.reply.text.strip(), f"{n}번째 run 의 말이 비었음"

    # 이어 간 run 은 맥락에 (질문, 답) 을 한 쌍씩 쌓는다. 상한은 이 길이로 세서, 안 쌓이면 상한에
    # 닿지 않는다
    question = _COUGH.question
    for n, result in enumerate(vague_chain.results[:-1], start=1):
        pending = result.pending
        assert pending is not None  # 맥락이 끝나면 run 을 더 잇지 않아 마지막 전에는 늘 있다
        assert pending.hint_text == _COUGH.hint_text, f"{n}번째 맥락의 조각이 바뀜"
        assert pending.transcript[-2:] == (question, _VAGUE_ANSWERS[n - 1]), f"{n}번째 답이 안 쌓임"
        assert len(pending.transcript) == 2 * n, f"{n}번째 맥락의 칸 수가 틀림"
        assert not result.ask_limit_reached, f"상한 전({n}번째)에 상한 표시"
        question = pending.question

    last = vague_chain.last
    assert last.pending is None, "상한까지 답했는데 맥락이 남음"
    if not vague_chain.asked_at_limit:
        assert not last.ask_limit_reached, "상한에서 또 묻지 않았는데 상한 표시"


def test_상한에서_또_물으면_저장하지_않은_run_만_상한으로_센다(vague_chain: Chain) -> None:
    last = vague_chain.last
    if not vague_chain.asked_at_limit:
        kind = last.reply.kind if last.reply else None
        n = len(vague_chain.results)
        pytest.skip(
            f"모델이 {n}번째 답에서 끝냄(kind={kind}). 상한에서 또 묻지 않아 이번엔 확인 못 함"
        )
    # 저장한 뒤 물었으면 상한이 아니다. 그 조각은 끝났고 pipeline 이 맥락 없는 질문으로 내린다
    if _wrote(last):
        assert not last.ask_limit_reached, "저장한 run 을 상한으로 셈"
    else:
        assert last.ask_limit_reached, "상한에서 또 물었는데 표시가 없음"


def test_상한_사슬이_저장_없이_말로_끝나면_저장했다고_하지_않는다(vague_chain: Chain) -> None:
    # "기록할게요" 라고 하고 저장하지 않으면 보호자는 기록된 줄 안다(C05 와 같은 기준).
    # 질문으로 끝났거나 저장하고 끝났으면 볼 것이 없다
    last = vague_chain.last
    reply = last.reply
    if reply is None or reply.kind != "message" or _wrote(last):
        return
    assert not _claims_save(reply.text), f"저장 없이 저장했다고 말함: {reply.text!r}"
