"""Memory Agent(가짜 LLM) -> DbMemoryStore -> DB 저장 -> Curator 전체 경로.

pipeline.handle_input 을 FakeLLM 으로 돌려 create_observation_food tool call 을 발행하면
DbMemoryStore 가 실제 DB 에 관찰을 쓰고, commit 콜백으로 확정한 뒤,
Curator(DbCuratorStore) 가 embedding + profile 연결까지 하는지 검증한다.
"""

import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import pipeline
from app.agents.common.llm_client import LLMResponse, LLMUnavailableError
from app.agents.food.context import FoodContext
from app.agents.food.store import InMemoryProfile, in_memory_ports
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory_bridge import StoreFoodMemory
from app.agents.pipeline import Saved, handle_input
from app.agents.run_writes import RunWrites, record_food_writes
from app.agents.suggestion_save import InMemorySuggestions
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ObservationFood
from app.domains.memory.profile.models import ProfileAffinity, ProfileState
from app.domains.memory.store.db_store import DbMemoryStore
from tests.eval.agents.routing_cases import CASES_BY_ID, answer_output

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=KST)
TODAY = date(2026, 9, 26)
RUN_ID = "run-db-1"


# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
async def family(session: AsyncSession):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="DB연결", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


# ── fake LLM helpers ────────────────────────────────────────────


def _reply(content: str) -> LLMResponse:
    message = SimpleNamespace(content=content, tool_calls=None)
    return LLMResponse(message=message, usage={}, latency_ms=1)


def _tools(*calls: SimpleNamespace) -> LLMResponse:
    message = SimpleNamespace(content=None, tool_calls=list(calls))
    return LLMResponse(message=message, usage={}, latency_ms=1)


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    raw = json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


class FakeLLM:
    """정해둔 응답을 차례로 돌려준다. 예외를 넣어 두면 그 차례에 던진다."""

    def __init__(self, *items: LLMResponse | Exception) -> None:
        self._queue = list(items)

    async def chat(self, **kwargs: Any) -> LLMResponse:
        item = self._queue.pop(0) if self._queue else _reply("끝")
        if isinstance(item, Exception):
            raise item
        return item


def _supervisor_llm(case_id: str) -> FakeLLM:
    output = answer_output(CASES_BY_ID[case_id])
    return FakeLLM(_tools(_call("s1", "route", output.model_dump(mode="json", exclude_none=True))))


def _answer(reply: dict[str, Any]) -> LLMResponse:
    return _reply(json.dumps({"pending_hint": None, **reply}, ensure_ascii=False))


# ── fake curator deps ───────────────────────────────────────────


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


class _FakeJudge:
    async def judge(self, *, subject: str, domain: str, candidates: Any) -> Any:
        from app.agents.curator.embedding.judge import JudgeAnswer

        return JudgeAnswer(choice="none", model="fake")


# ── fake domain context ─────────────────────────────────────────


class _FakeContext:
    def for_task(self) -> "_FakeContext":
        return _FakeContext()


_STRAWBERRY = {
    "raw_text": "딸기 잘 먹었어",
    "observed_on": "오늘",
    "subject": "딸기",
    "polarity": 1,
    "confidence_source": "parent_direct",
}


def _memory_context(store: DbMemoryStore, owner: Parent, child: Child) -> AgentContext:
    return AgentContext(
        child_id=child.id, source_writer=owner.id, now=NOW, timezone=KST, store=store
    )


def _saves_strawberry(*then: LLMResponse | Exception) -> FakeLLM:
    return FakeLLM(_tools(_call("a", "create_observation_food", _STRAWBERRY)), *then)


async def _food_subjects(session: AsyncSession, child: Child) -> list[str]:
    rows = await session.scalars(
        select(ObservationFood.subject).where(ObservationFood.child_id == child.id)
    )
    return list(rows)


# ── test ────────────────────────────────────────────────────────


class TestMemoryToCurator:
    """DbMemoryStore 로 관찰을 저장한 뒤 Curator 가 동작하는 전체 파이프라인."""

    async def test_pipeline_saves_to_db_and_curator_links(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child = family

        store = DbMemoryStore(session, child_id=child.id)

        memory_context = AgentContext(
            child_id=child.id,
            source_writer=owner.id,
            now=NOW,
            timezone=KST,
            store=store,
        )

        food_ports = in_memory_ports(
            profile=InMemoryProfile({child.id: date(2023, 1, 1)}),
            memory=StoreFoodMemory(store),
        )
        writes = RunWrites()
        food_ports = record_food_writes(food_ports, writes)
        food_context = FoodContext(
            child_id=child.id, run_id=RUN_ID, now=NOW, timezone=KST, ports=food_ports
        )

        committed = False

        async def commit() -> None:
            nonlocal committed
            await session.flush()
            committed = True

        events: list[Any] = []

        # Memory LLM: create_observation_food -> 응답
        memory_llm = FakeLLM(
            _tools(
                _call(
                    "a",
                    "create_observation_food",
                    {
                        "raw_text": "딸기 잘 먹었어",
                        "observed_on": "오늘",
                        "subject": "딸기",
                        "polarity": 1,
                        "confidence_source": "parent_direct",
                    },
                )
            ),
            _answer({"reply": "딸기 기록 남겼어요.", "kind": "message"}),
        )

        result = await handle_input(
            CASES_BY_ID["RC01"].text,
            memory_context,
            {"food": food_context},
            run_id=RUN_ID,
            supervisor_client=_supervisor_llm("RC01"),
            memory_client=memory_llm,
            emit=events.append,
            commit=commit,
            writes=writes,
            save_suggestions=InMemorySuggestions(),
        )

        # ── pipeline 성공 ──
        assert result.ok
        assert committed

        # ── DB 에 관찰이 저장됨 ──
        obs_rows = (
            await session.scalars(
                select(ObservationFood).where(ObservationFood.child_id == child.id)
            )
        ).all()
        assert len(obs_rows) >= 1
        row = obs_rows[0]
        assert row.subject == "딸기"
        assert row.embedding is None  # curator 전
        assert row.affinity_id is None

        # ── Saved 이벤트가 나왔는지 ──
        saved = [e for e in events if isinstance(e, Saved)]
        assert saved, "Saved 이벤트가 나와야 한다"

        # ── Curator 실행 ──
        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore
        from app.domains.memory.curator.recompute import recompute_after_linking

        curator_store = DbCuratorStore(session)
        link_result = await link_observations(
            curator_store,
            _FakeEmbedder(),
            _FakeJudge(),
            child_id=child.id,
            birth_date=date(2023, 1, 1),
        )
        if link_result.affected_profile_ids:
            await recompute_after_linking(session, result=link_result, today=TODAY)

        # ── 관찰에 embedding + affinity_id 가 채워짐 ──
        await session.flush()
        row = await session.get(ObservationFood, obs_rows[0].id, options=[])  # 캐시 무시
        assert row.embedding is not None, "embedding 이 채워져야 한다"
        assert row.affinity_id is not None, "profile 에 연결되어야 한다"

        # ── Profile 이 생성됨 ──
        profile = await session.get(ProfileAffinity, row.affinity_id)
        assert profile is not None
        assert profile.state == ProfileState.CANDIDATE
        assert profile.merge_key == "딸기"


class TestCommitPoint:
    """Memory 직후 commit 이 run 의 확정 지점이다 (#257).

    러너가 끝에서 commit 안 된 세션을 닫는(= rollback) 자리는 session.rollback() 으로 흉내 낸다.
    """

    async def test_Memory_commit_뒤_Food_가_죽어도_관찰은_남는다(
        self, session: AsyncSession, family: tuple, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner, child = family
        store = DbMemoryStore(session, child_id=child.id)
        order: list[str] = []

        async def commit() -> None:
            await session.commit()
            order.append("commit")

        async def food_down(task: Any, context: Any, **kwargs: Any) -> Any:
            order.append("food")
            raise RuntimeError("food down")

        monkeypatch.setitem(pipeline._RUNNERS, "food", food_down)

        result = await handle_input(
            CASES_BY_ID["RC01"].text,
            _memory_context(store, owner, child),
            {"food": _FakeContext()},
            run_id=RUN_ID,
            supervisor_client=_supervisor_llm("RC01"),
            memory_client=_saves_strawberry(_answer({"reply": "기록했어요.", "kind": "message"})),
            commit=commit,
            save_suggestions=InMemorySuggestions(),
        )

        assert result.committed and result.failed is None
        assert result.partial is not None  # Food 만 빠졌다
        assert order == ["commit", "food"]  # Food 는 commit 뒤에 돈다
        await session.rollback()  # 러너가 끝에서 commit 안 된 것을 되돌리는 자리
        assert await _food_subjects(session, child) == ["딸기"]

    async def test_Memory_가_commit_전에_죽으면_관찰이_남지_않는다(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child = family
        store = DbMemoryStore(session, child_id=child.id)
        commits: list[str] = []

        async def commit() -> None:
            commits.append("commit")
            await session.commit()

        result = await handle_input(
            CASES_BY_ID["RC01"].text,
            _memory_context(store, owner, child),
            {"food": _FakeContext()},
            run_id=RUN_ID,
            supervisor_client=_supervisor_llm("RC01"),
            # 관찰 하나를 flush 한 뒤 다음 모델 호출에서 죽는다
            memory_client=_saves_strawberry(LLMUnavailableError("망")),
            commit=commit,
            save_suggestions=InMemorySuggestions(),
        )

        assert result.failed is not None and result.failed.reason == "llm_unavailable"
        assert commits == []
        assert store.wrote is True  # flush 는 됐다 — 그래서 되돌리는 것이 의미가 있다
        await session.rollback()
        assert await _food_subjects(session, child) == []

    async def test_이어받기_run_도_Memory_직후_commit_된다(
        self, session: AsyncSession, family: tuple
    ) -> None:
        owner, child = family
        store = DbMemoryStore(session, child_id=child.id)
        commits: list[str] = []

        async def commit() -> None:
            await session.commit()
            commits.append("commit")

        result = await handle_input(
            "반 개 먹었어",
            _memory_context(store, owner, child),
            {},
            run_id=RUN_ID,
            memory_client=_saves_strawberry(_answer({"reply": "기록했어요.", "kind": "message"})),
            continuation=PendingMemoryContext(
                "요즘 딸기 먹어?", "얼마나 먹었어요?", WorkType.OBSERVE
            ),
            commit=commit,
            save_suggestions=InMemorySuggestions(),
        )

        assert result.committed
        assert commits == ["commit"]
        await session.rollback()
        assert await _food_subjects(session, child) == ["딸기"]
