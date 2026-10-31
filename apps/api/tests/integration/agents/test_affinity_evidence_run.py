"""mock 프로필(DB 의 profile_affinity)로 Agent 가 근거를 실제로 뽑아오는지 끝까지 본다 (#572).

러너 · 05 와 같은 배선이다 — `DbAffinityReader` 를 entrypoint 로 넘기고, 기억 다리가 그날 기준으로
읽은 관심을 Agent 의 기억 조회 tool 이 근거로 돌려주는지, 그 근거가 저장 뒤 `suggestion_evidence` 에
남는지까지 본다. 모델만 정해 둔 응답을 내는 가짜다. 아이 · 관심 이름은 전부 가짜다.

mock 프로필 (기준일 2026-10-14)
    첫째 (30개월)   놀이 공룡 — 3일 전 confirmed        → 근거로 나간다
                    놀이 블록 — 22일 전 confirmed       → 21일을 넘어 근거에서 빠진다
                    놀이 퍼즐 — archived                → 읽지 않는다
                    식사 딸기 — 3일 전 confirmed        → Activity 근거가 아니다
    다른 아이       놀이 기차 — 3일 전 confirmed        → 읽지 않는다
"""

import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.agents import entrypoint
from app.agents.activity.agent import run
from app.agents.activity.context import ActivityContext
from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.memory.store import InMemoryStore
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationFood,
)
from app.domains.memory.profile.agent_reader import DbAffinityReader
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.safety.agent_reader import DbSafetyReader
from app.domains.suggestion.models import SuggestionEvidence
from app.domains.suggestion.repository import save_agent_suggestions

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=KST)  # 수요일
TODAY = NOW.date()
BIRTH = date(2024, 4, 14)  # 30개월 — 관심(profile_affinity)을 읽는 월령
PLAYS = ("공룡 그림자놀이", "공룡 블록 쌓기", "공룡 색종이 접기")


class FakeLLM:
    """정해 둔 응답을 순서대로 낸다. 받은 messages 는 호출마다 복사해 둔다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.seen.append([dict(message) for message in messages])
        if self._queue:
            return self._queue.pop(0)
        return LLMResponse(
            message=SimpleNamespace(content="더 할 말이 없어요.", tool_calls=None),
            usage={},
            latency_ms=1,
        )


def tools(*calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    tool_calls = [
        SimpleNamespace(
            id=f"call-{i}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
        )
        for i, (name, args) in enumerate(calls)
    ]
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=tool_calls), usage={}, latency_ms=1
    )


def propose(contents: tuple[str, ...], evidence: list[dict[str, str]]) -> LLMResponse:
    return tools(
        (
            "propose_activity_candidates",
            {
                "candidates": [
                    {
                        "content": content,
                        "setting": "indoor",
                        "day": "today",
                        "materials": [],
                        "physical_intensity": "low",
                        "involves_food": False,
                        "caregiver_role": "together",
                        "reason": "공룡을 좋아하는 기록이 있어서 골랐어요",
                        "evidence": evidence,
                    }
                    for content in contents
                ]
            },
        )
    )


def tool_output(llm: FakeLLM, call_index: int) -> str:
    """`call_index` 번째 모델 호출이 받은 tool 결과 전부 — 조회 tool 이 모델에게 보여 준 것이다."""
    return "\n".join(str(m["content"]) for m in llm.seen[call_index] if m["role"] == "tool")


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return same


async def make_child(session, nickname: str):
    owner = Parent()
    session.add(owner)
    await session.flush()
    return await create_child(
        session,
        owner_parent_id=owner.id,
        nickname=nickname,
        birth_date=BIRTH,
        relation=ParentChildRelation.MOTHER,
    )


@pytest.fixture
async def profile(session):
    """mock 프로필 — 아이 둘과 관심 여섯 줄. 관심 id 는 근거 대조에 쓴다."""
    mine = await make_child(session, "첫째")
    other = await make_child(session, "다른 아이")
    made: dict[str, ProfileAffinity] = {}

    def add(owner, domain: MemoryDomain, key: str, state: ProfileState, *, days_ago: int):
        row = ProfileAffinity(
            child_id=owner.id,
            merge_key=key,
            domain=domain,
            state=state,
            polarity=1,
            strength=0.9,
            last_observed_on=TODAY - timedelta(days=days_ago),
        )
        session.add(row)
        made[key] = row

    add(mine, MemoryDomain.ACTIVITY, "공룡", ProfileState.CONFIRMED, days_ago=3)
    add(mine, MemoryDomain.ACTIVITY, "블록", ProfileState.CONFIRMED, days_ago=22)
    add(mine, MemoryDomain.ACTIVITY, "퍼즐", ProfileState.ARCHIVED, days_ago=3)
    add(mine, MemoryDomain.FOOD, "딸기", ProfileState.CONFIRMED, days_ago=3)
    add(other, MemoryDomain.ACTIVITY, "기차", ProfileState.CONFIRMED, days_ago=3)
    await session.flush()
    # 묶인 active 관찰이 없는 관심은 어댑터가 싣지 않는다 (#626) — 관심마다 마지막 관찰 한 건을 둔다
    for row in made.values():
        model, fields = (
            (ObservationFood, {})
            if row.domain == MemoryDomain.FOOD
            else (ObservationActivity, {"activity": row.merge_key})
        )
        day = row.last_observed_on
        session.add(
            model(
                child_id=row.child_id,
                raw_text="원문",
                subject=row.merge_key,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
                affinity_id=row.id,
                **fields,
            )
        )
    await session.flush()
    return SimpleNamespace(child=mine, other=other, rows=made)


def activity_of(profile, opener, *, with_affinity: bool = True) -> ActivityContext:
    """러너 · 05 가 만드는 것과 같은 배선. 안전 조회는 빈 `health_safety` 라 닫히지 않는다."""
    contexts, _ = entrypoint._domain_contexts(
        child_id=profile.child.id,
        run_id="run-1",
        now=NOW,
        store=InMemoryStore(now=NOW),
        birth_date=BIRTH,
        child_health_granted=True,
        location=None,
        activity_weather=None,
        domain_store=None,
        activity_safety=DbSafetyReader(opener),
        affinity_reader=DbAffinityReader(opener) if with_affinity else None,
    )
    return contexts["activity"]


def activity_task() -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="activity",
        task_type=None,
        request_texts=("주말에 집에서 뭐 하고 놀까",),
    )


def cite(profile, key: str) -> list[dict[str, str]]:
    return [{"id": str(profile.rows[key].id), "note": "기억에서 읽은 관심"}]


async def test_기록이_없어도_관심_하나로_Activity_가_근거를_뽑아온다(session, opener, profile):
    llm = FakeLLM(
        tools(("search_activity_memory", {})),
        propose(PLAYS, cite(profile, "공룡")),
    )

    result = await run(activity_task(), activity_of(profile, opener), client=llm)

    assert result.status == "completed"
    shown = tool_output(llm, 1)  # 기억 조회 결과를 받은 뒤의 모델 호출
    assert "공룡" in shown
    assert str(profile.rows["공룡"].id) in shown
    assert [draft.content for draft in result.suggestions] == list(PLAYS)
    # 관찰이 0건이어도 관심이 근거라 개인화로 나간다
    assert {draft.kind for draft in result.suggestions} == {"personalized"}


async def test_Activity_가_안_읽는_관심은_모델에게_보이지_않는다(session, opener, profile):
    llm = FakeLLM(
        tools(("search_activity_memory", {})),
        propose(PLAYS, cite(profile, "공룡")),
    )

    await run(activity_task(), activity_of(profile, opener), client=llm)

    shown = tool_output(llm, 1)
    assert "블록" not in shown  # 마지막 관찰이 22일 전 — 그날 기준 archived
    assert "퍼즐" not in shown  # archived
    assert "딸기" not in shown  # 다른 도메인(food)
    assert "기차" not in shown  # 다른 아이


async def test_근거로_단_관심은_저장하면_suggestion_evidence_에_남는다(session, opener, profile):
    llm = FakeLLM(
        tools(("search_activity_memory", {})),
        propose(PLAYS, cite(profile, "공룡")),
    )
    result = await run(activity_task(), activity_of(profile, opener), client=llm)

    ids = await save_agent_suggestions(
        session,
        child_id=profile.child.id,
        run_id="run-1",
        payloads=[draft.to_payload() for draft in result.suggestions],
        now=NOW,
    )
    evidence = (
        await session.scalars(
            select(SuggestionEvidence).where(SuggestionEvidence.suggestion_id.in_(ids))
        )
    ).all()

    assert {row.suggestion_id for row in evidence} == set(ids)  # 근거 0행인 추천이 없다
    assert {(row.source_kind, row.source_id) for row in evidence} == {
        ("profile_affinity", profile.rows["공룡"].id)
    }


async def test_낡은_관심의_id_를_인용하면_후보가_거절된다(session, opener, profile):
    """모델이 22일 지난 블록의 id 를 인용해도 이번 run 에 조회한 근거가 아니라 통과하지 못한다."""
    llm = FakeLLM(
        tools(("search_activity_memory", {})),
        propose(PLAYS, cite(profile, "블록")),
        propose(PLAYS, cite(profile, "공룡")),
    )

    result = await run(activity_task(), activity_of(profile, opener), client=llm)

    rejected = json.loads(next(m["content"] for m in reversed(llm.seen[2]) if m["role"] == "tool"))
    assert rejected["success"] is False
    assert [draft.content for draft in result.suggestions] == list(PLAYS)


async def test_관심_어댑터를_안_넘기면_같은_아이도_기억이_비어_있다(session, opener, profile):
    """대조군 — 어댑터가 빠지면 기억 화면에는 보이는 관심이 추천 근거에서 조용히 사라진다."""
    llm = FakeLLM(tools(("search_activity_memory", {})))

    await run(activity_task(), activity_of(profile, opener, with_affinity=False), client=llm)

    shown = tool_output(llm, 1)
    assert "NO_RECORDS" in shown
    assert "공룡" not in shown
