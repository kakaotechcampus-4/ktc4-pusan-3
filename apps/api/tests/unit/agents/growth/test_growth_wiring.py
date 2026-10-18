"""Growth 가 pipeline 에서 도는지 — Supervisor 라벨 → routing → Growth run() → 화면 결과.

LLM 은 정해 둔 응답을 내는 가짜다. 포트는 entrypoint 가 만드는 것 그대로다(`_domain_contexts`) —
안전은 넘긴 어댑터(안 넘기면 미연결), 성장 기록은 넘긴 InMemory, 기억은 같은 run 의 Memory store.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents import entrypoint, pipeline
from app.agents.common.datetime_rules import build_observed_range
from app.agents.common.llm_client import LLMResponse
from app.agents.common.ports import SafetyLookupError
from app.agents.growth.readouts import READOUTS as GROWTH_READOUTS
from app.agents.growth.store.inmemory import InMemoryGrowthDocs, InMemoryGrowthLog
from app.agents.growth.store.ports import GrowthMeasurement, SafetyEntry
from app.agents.growth.tools.docs import match_chain, routine_chains
from app.agents.growth.tools.routine import pick_next_step
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.agents.memory_bridge import StoreGrowthMemory
from tests.unit.agents.growth.support import ProposesExamples, fixture_docs

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=KST)
TODAY = NOW.date()
CHILD = UUID(int=1)
PARENT = UUID(int=2)
BIRTH_36 = date(2023, 10, 15)


@pytest.fixture(autouse=True)
def _fixture_docs(monkeypatch):
    """entrypoint 는 시드를 읽는다. 검수 결과로 시드 사슬이 바뀌어도 이 파일은 고정 행으로 돈다."""
    monkeypatch.setattr(InMemoryGrowthDocs, "from_seed", classmethod(lambda cls: fixture_docs()))


class FakeLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.calls = 0

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        if self._queue:
            return self._queue.pop(0)
        return LLMResponse(
            message=SimpleNamespace(content="끝", tool_calls=None), usage={}, latency_ms=1
        )


_ids = iter(range(1, 10_000))


def call(name: str, args: dict[str, Any]) -> LLMResponse:
    tool_call = SimpleNamespace(
        id=f"c{next(_ids)}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=[tool_call]), usage={}, latency_ms=1
    )


def route(text: str, label: str) -> FakeLLM:
    segment = {"text": text, "kind": "request", "agent": "growth", "growth_task": label}
    return FakeLLM(call("route", {"segments": [segment]}))


async def _no_commit() -> None:
    return None


def contexts(store: InMemoryStore, *, granted: bool = True, **kwargs: Any) -> dict[str, Any]:
    built, _ = entrypoint._domain_contexts(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        store=store,
        birth_date=BIRTH_36,
        child_health_granted=granted,
        location=None,
        activity_weather=None,
        domain_store=None,
        **kwargs,
    )
    return built


async def handle(
    raw: str,
    label: str,
    store: InMemoryStore,
    growth_llm: Any,
    *,
    saved: entrypoint.InMemorySuggestions | None = None,
    granted: bool = True,
    **ports,
):
    events: list[Any] = []
    await pipeline.handle_input(
        raw,
        AgentContext(child_id=CHILD, source_writer=PARENT, now=NOW, timezone=KST, store=store),
        contexts(store, granted=granted, **ports),
        run_id="run-1",
        supervisor_client=route(raw, label),
        memory_client=FakeLLM(),
        domain_clients={"growth": growth_llm},
        emit=events.append,
        commit=_no_commit,
        save_suggestions=saved or entrypoint.InMemorySuggestions(),
    )
    return [e for e in events if isinstance(e, pipeline.AgentResult)]


def measurement(n: int, day: date, height: str, weight: str) -> GrowthMeasurement:
    return GrowthMeasurement(
        id=UUID(int=500 + n),
        child_id=CHILD,
        measured_on=day,
        height_cm=Decimal(height),
        weight_kg=Decimal(weight),
    )


async def test_성장_추이는_모델_없이_화면_결과로_나간다():
    log = InMemoryGrowthLog(
        [
            measurement(1, date(2026, 4, 15), "95.0", "14.0"),
            measurement(2, date(2026, 10, 1), "100.0", "15.5"),
        ]
    )
    growth_llm = FakeLLM()
    (shown,) = await handle(
        "잘 크고 있어?", "growth_review", InMemoryStore(now=NOW), growth_llm, growth_log=log
    )

    assert (shown.agent, shown.task_type, shown.status) == ("growth", "growth_review", "completed")
    assert shown.readouts[0].kind == "growth_delta"
    assert growth_llm.calls == 0


async def test_안전_어댑터를_안_넘기면_교육은_닫힌_안내만_나간다():
    growth_llm = FakeLLM()
    (shown,) = await handle(
        "숫자 놀이 추천해줘", "learning_suggestion", InMemoryStore(now=NOW), growth_llm
    )

    assert shown.status == "blocked"
    assert growth_llm.calls == 0


POLLEN = SafetyEntry(kind="allergy", label="꽃가루", status="active")


class CountingSafety:
    """`DbSafetyReader.growth_safety` 자리. 몇 번 읽었는지 남긴다."""

    def __init__(self, *entries: SafetyEntry, fail: bool = False) -> None:
        self.entries = list(entries)
        self.fail = fail
        self.calls = 0

    async def growth_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        self.calls += 1
        if self.fail:
            raise SafetyLookupError("못 읽음")
        return list(self.entries)


async def test_안전_어댑터를_넘기면_교육이_열리고_추천이_저장된다():
    growth_llm = ProposesExamples()
    saved = entrypoint.InMemorySuggestions()
    safety = CountingSafety(POLLEN)

    (shown,) = await handle(
        "숫자 놀이 추천해줘",
        "learning_suggestion",
        InMemoryStore(now=NOW),
        growth_llm,
        saved=saved,
        growth_safety=safety,
    )

    assert (shown.task_type, shown.status) == ("learning_suggestion", "completed")
    assert len(shown.suggestion_ids) == 3 and shown.kind == "general"
    assert shown.readouts == ()  # Growth 는 알레르기 확인 문구를 내지 않는다 — 표로 막는다
    assert safety.calls == 1  # 건강정보는 run 당 한 번만 읽는다
    # 일반 추천의 근거는 문서 행이다 — 근거 0행으로 저장되지 않는다 (루트 §2)
    (batch,) = saved.batches
    assert all(any(c.ref.kind == "growth_doc" for c in d.citations) for d in batch.drafts)


async def test_꽃가루_아이에게는_표_낱말이_든_후보가_빠지고_이유는_모델에_가지_않는다():
    growth_llm = ProposesExamples(titles=("나뭇잎 세기", "색깔 찾기", "모양 맞추기"))
    saved = entrypoint.InMemorySuggestions()

    (shown,) = await handle(
        "숫자 놀이 추천해줘",
        "learning_suggestion",
        InMemoryStore(now=NOW),
        growth_llm,
        saved=saved,
        growth_safety=CountingSafety(POLLEN),
    )

    (batch,) = saved.batches
    titles = [d.content.split("\n")[0] for d in batch.drafts]
    assert titles == ["색깔 찾기", "모양 맞추기"]  # 재호출에도 같은 후보라 둘만 남는다
    assert [r.code for r in shown.readouts] == ["fewer"]  # 개수 안내뿐, 확인 문구는 없다
    sent = json.dumps(growth_llm.seen, ensure_ascii=False)
    assert "꽃가루" not in sent and "알레르기" not in sent  # 이름도 이유도 모델 입력에 없다


async def test_동의가_없으면_건강정보를_읽지_않고_교육이_그대로_나간다():
    safety = CountingSafety(POLLEN)

    (shown,) = await handle(
        "숫자 놀이 추천해줘",
        "learning_suggestion",
        InMemoryStore(now=NOW),
        ProposesExamples(),
        granted=False,
        growth_safety=safety,
    )

    assert shown.status == "completed" and len(shown.suggestion_ids) == 3
    assert safety.calls == 0


async def test_안전_조회가_실패하면_교육만_닫히고_성장_추이는_나간다():
    raw = "잘 크고 있어? 숫자 놀이도 추천해줘"
    supervisor = FakeLLM(
        call(
            "route",
            {
                "segments": [
                    {
                        "text": "잘 크고 있어?",
                        "kind": "request",
                        "agent": "growth",
                        "growth_task": "growth_review",
                    },
                    {
                        "text": "숫자 놀이도 추천해줘",
                        "kind": "request",
                        "agent": "growth",
                        "growth_task": "learning_suggestion",
                    },
                ]
            },
        )
    )
    log = InMemoryGrowthLog(
        [
            measurement(1, date(2026, 4, 15), "95.0", "14.0"),
            measurement(2, date(2026, 10, 1), "100.0", "15.5"),
        ]
    )
    store = InMemoryStore(now=NOW)
    growth_llm = FakeLLM()
    events: list[Any] = []

    await pipeline.handle_input(
        raw,
        AgentContext(child_id=CHILD, source_writer=PARENT, now=NOW, timezone=KST, store=store),
        contexts(store, growth_log=log, growth_safety=CountingSafety(fail=True)),
        run_id="run-1",
        supervisor_client=supervisor,
        memory_client=FakeLLM(),
        domain_clients={"growth": growth_llm},
        emit=events.append,
        commit=_no_commit,
        save_suggestions=entrypoint.InMemorySuggestions(),
    )

    shown = {e.task_type: e for e in events if isinstance(e, pipeline.AgentResult)}
    assert shown["learning_suggestion"].status == "blocked"
    assert shown["learning_suggestion"].readouts == (GROWTH_READOUTS.render("blocked.safety"),)
    assert shown["growth_review"].status == "completed"
    assert growth_llm.calls == 0


async def test_도서는_포트가_없어_닫힌_안내만_나간다():
    (shown,) = await handle(
        "공룡 책 추천해줘", "book_suggestion", InMemoryStore(now=NOW), FakeLLM()
    )

    assert shown.status == "blocked"


async def test_루틴은_같은_run_의_기록을_근거로_추천을_저장한다():
    store = InMemoryStore(now=NOW)
    day = TODAY - timedelta(days=1)
    await store.create_observation(
        domain="routine",
        child_id=CHILD,
        source_writer=PARENT,
        raw_text="어제 양치 어른이 다 해 줬어",
        observed_on=day,
        observed_range=build_observed_range(day),
        fields={
            "subject": "양치",
            "routine_category": "self_care",
            "polarity": 0,
            "assistance_level": "full_assist",
        },
    )
    (observation,) = await StoreGrowthMemory(store).routine(
        child_id=CHILD, date_from=day, date_to=TODAY
    )
    built = contexts(store)
    chains = await routine_chains(built["growth"].ports.docs, months=36, category="self_care")
    step = pick_next_step(match_chain(chains, "양치"), assistance_level="full_assist")
    growth_llm = FakeLLM(
        call("search_routine_memory", {}),
        call(
            "propose_routine_plan",
            {
                "category": "self_care",
                "subject": "양치",
                "items": [
                    {
                        "step_id": str(step.row.id),
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": [
                            {"id": str(observation.id), "note": "어제 어른이 다 해 줬어요"}
                        ],
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            },
        ),
    )

    saved = entrypoint.InMemorySuggestions()
    (shown,) = await handle(
        "양치 혼자 하게 하고 싶어", "routine_coaching", store, growth_llm, saved=saved
    )

    assert shown.status == "completed" and len(shown.suggestion_ids) == 1
    assert shown.kind == "personalized"
    # 저장된 추천이 실제로 그 관찰을 근거로 달고 나갔는지 — id 개수만 보면 근거 0행도 통과한다
    (draft,) = saved.batches[0].drafts
    cited = {(c.ref.kind, c.ref.id) for c in draft.citations}
    assert ("observation_routine", observation.id) in cited


class ReadsToolResult:
    """조회 tool 결과(id · step_id)를 읽어 그대로 제출하는 Growth 모델 가짜.

    같은 run 에서 Memory 가 방금 저장한 관찰의 id 는 미리 알 수 없어서, 응답을 정해 두지 않고
    첫 호출의 tool 결과에서 꺼내 쓴다.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return call("search_routine_memory", {})
        result = json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])
        record = result["data"]["records"][0]
        (step,) = result["data"]["next_steps"]
        return call(
            "propose_routine_plan",
            {
                "category": "self_care",
                "subject": "양치",
                "items": [
                    {
                        "step_id": step["step_id"],
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": [{"id": record["id"], "note": "방금 말한 양치 기록이에요"}],
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            },
        )


async def test_같은_run_에서_Memory_가_저장한_기록을_Growth_가_근거로_잡는다():
    """혼합형 — 저장이 검색보다 먼저다 (루트 §4). 방금 말한 양치가 같은 run 의 루틴 근거가 된다."""
    raw = "어제 양치는 어른이 다 해 줬어. 양치 혼자 하게 하고 싶어"
    supervisor = FakeLLM(
        call(
            "route",
            {
                "segments": [
                    {"text": "어제 양치는 어른이 다 해 줬어", "kind": "record", "work": "observe"},
                    {
                        "text": "양치 혼자 하게 하고 싶어",
                        "kind": "request",
                        "agent": "growth",
                        "growth_task": "routine_coaching",
                    },
                ]
            },
        )
    )
    memory = FakeLLM(
        call(
            "create_observation_routine",
            {
                "raw_text": "어제 양치는 어른이 다 해 줬어",
                "observed_on": "오늘",
                "subject": "양치",
                "routine_category": "self_care",
                "assistance_level": "full_assist",
            },
        ),
        LLMResponse(
            message=SimpleNamespace(content="기록했어요.", tool_calls=None), usage={}, latency_ms=1
        ),
    )
    store = InMemoryStore(now=NOW)
    saved = entrypoint.InMemorySuggestions()
    events: list[Any] = []

    await pipeline.handle_input(
        raw,
        AgentContext(child_id=CHILD, source_writer=PARENT, now=NOW, timezone=KST, store=store),
        contexts(store),
        run_id="run-1",
        supervisor_client=supervisor,
        memory_client=memory,
        domain_clients={"growth": ReadsToolResult()},
        emit=events.append,
        commit=_no_commit,
        save_suggestions=saved,
    )

    (observation,) = await StoreGrowthMemory(store).routine(
        child_id=CHILD, date_from=TODAY - timedelta(days=1), date_to=TODAY
    )
    (shown,) = [e for e in events if isinstance(e, pipeline.AgentResult)]
    assert shown.status == "completed" and len(shown.suggestion_ids) == 1
    (draft,) = saved.batches[0].drafts
    assert ("observation_routine", observation.id) in {
        (c.ref.kind, c.ref.id) for c in draft.citations
    }


async def test_도서는_정보나루_어댑터가_있으면_고른_책을_책_근거와_함께_저장한다():
    from app.agents.growth.store.inmemory import InMemoryIssuedBooks
    from app.rules.book_evidence import book_evidence_id
    from tests.unit.agents.growth.support import FakeRawSearch, raw_book

    raw = FakeRawSearch({"공룡": [raw_book(n) for n in range(1, 4)]})
    items = [
        {"isbn": raw_book(n).isbn13, "evidence": [], "reason": "공룡이 나오는 그림책이에요"}
        for n in range(1, 4)
    ]
    growth_llm = FakeLLM(
        call("search_books", {"keywords": ["공룡"]}),
        call("propose_books", {"items": items}),
    )

    saved = entrypoint.InMemorySuggestions()
    (shown,) = await handle(
        "공룡 책 추천해줘",
        "book_suggestion",
        InMemoryStore(now=NOW),
        growth_llm,
        saved=saved,
        growth_books=raw,
        growth_issued_books=InMemoryIssuedBooks(),
    )

    assert shown.status == "completed" and len(shown.suggestion_ids) == 3
    assert shown.kind == "general"
    first = saved.batches[0].drafts[0]
    assert first.content == "책1(가나다)" and first.items == ("책1(가나다)",)
    assert [(c.ref.kind, c.ref.id) for c in first.citations] == [
        ("book_catalog", book_evidence_id(raw_book(1).isbn13))
    ]
    assert raw.calls == [("공룡", 50)]
