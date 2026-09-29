"""이어받기 라이브 eval. 답에 다른 말이 섞였을 때 Memory 가 그 부분을 남기는지 본다 (PR #175).
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/memory/test_continuation.py -m live -s

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient
from app.agents.memory.agent import MemoryAgentResult, run
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
_WHOLE = PendingMemoryContext("모래놀이하고 뭐 좀 먹었어", "무엇을 먹었어요?", WorkType.OBSERVE)


@dataclass(frozen=True)
class Case:
    case_id: str
    pending: PendingMemoryContext
    answer: str
    rows: dict[str, int]  # 끝난 뒤 도메인별 관찰 수. 적지 않은 도메인은 0
    leftover: bool
    kind: str | None = None  # 기대하는 reply.kind. None 이면 보지 않는다
    checks: tuple[Check, ...] = ()  # 답이 반영됐는지. 흔들리는 케이스에서도 실패로 막는다
    drafts: int = 0  # 끝난 뒤 일정 초안 수


_COUGH_ANSWERED = (_observed_on("health", TWO_DAYS_AGO),)

# 답의 날짜는 datetime_rules 가 읽는 표현으로 둔다. "3일 전" 은 아직 못 읽어서
# DATE_UNPARSEABLE 로 되묻게 되고, 그러면 섞인 말이 아니라 날짜 해석을 재게 된다
CASES = [
    Case("C01", _COUGH, "그저께부터", {"health": 1}, False, checks=_COUGH_ANSWERED),
    Case(
        "C02",
        _COUGH,
        "그저께부터. 그리고 오늘 수영장 다녀왔어",
        {"health": 1},
        True,
        checks=_COUGH_ANSWERED,
    ),
    Case("C03", _COUGH, "그저께부터. 저녁 뭐 먹일까?", {"health": 1}, True, checks=_COUGH_ANSWERED),
    Case("C04", _COUGH, "저녁 뭐 먹일까?", {}, True, kind="question"),
    Case("C05", _WHOLE, "떡볶이", {"activity": 1, "food": 1}, False),
    # 답이 뒤에 와도 답은 반영한다
    Case(
        "C06",
        _COUGH,
        "오늘 수영장 다녀왔어. 기침은 그저께부터야",
        {"health": 1},
        True,
        checks=_COUGH_ANSWERED,
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
    ),
]

# 결과가 흔들리는 케이스(2026-09-29 측정). 답 반영과 중복 저장 방지는 그래도 실패로 막고,
# "섞인 말은 남기고 leftover 로 알린다" 를 못 지킨 것만 xfail 로 둔다.
# 못 지킨 경우는 전부 섞인 말까지 처리하고 leftover=False 로 답했다. 저장과 안내가 맞아서
# 두 번 저장되지는 않는다
_FLAKY = {
    # 강등 경로 조각. "조각 안의 기록을 전부 저장한다" 를 헤더에 넣어 보면 C02 에서 섞인 기록까지
    # 저장해서(8번 중 5번) 넣지 않았다
    "C05": "조각 안의 두 번째 기록을 빠뜨릴 때가 있다 (11번 중 6번 통과)",
    "C06": "답이 뒤에 오면 앞의 섞인 기록까지 저장할 때가 있다 (9번 중 5번 통과)",
    "C08": "섞인 일정의 시각을 묻거나 초안을 만들 때가 있다 (9번 중 2번 통과)",
}


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
        f"pending={result.pending is not None} {result.final_message!r}"
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

    # 바라는 동작: 섞인 말은 처리하지 않고 leftover 로 알린다. 묻는다면 답할 맥락이 있는 질문이다
    # (저장한 뒤 섞인 일정의 시각을 물으면 맥락이 없어 보호자의 답이 400 을 받는다)
    answerable = result.reply.kind != "question" or result.pending is not None
    kept = (
        counts == expected
        and len(result.drafts) == case.drafts
        and result.leftover is case.leftover
        and answerable
    )
    if not kept and case.case_id in _FLAKY:
        pytest.xfail(_FLAKY[case.case_id])
    assert counts == expected
    assert len(result.drafts) == case.drafts
    assert result.leftover is case.leftover
    assert answerable, "답할 맥락이 없는 질문"
