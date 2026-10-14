"""Agent 결과 하나의 추천 저장 — 근거 검증 · 묶음 단위 · run_id · draft +24h."""

import uuid
from datetime import UTC, date, datetime
from typing import get_args

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range

from app.agents.common.refs import ChildRecordKind, DocKind, EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionDraft
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.suggestion import repository
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionEvidence,
    SuggestionKind,
    SuggestionStatus,
)
from app.domains.suggestion.repository import (
    SUGGESTION_TTL,
    SuggestionEvidenceError,
    save_agent_suggestions,
)

NOW = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
RUN = "run-1"


@pytest.fixture
async def kids(session):
    owner, stranger = Parent(), Parent()
    session.add_all([owner, stranger])
    await session.flush()
    child = await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )
    other = await create_child(
        session,
        owner_parent_id=stranger.id,
        nickname="다른 아이",
        birth_date=date(2024, 1, 1),
        relation=ParentChildRelation.FATHER,
    )
    return owner, child, other


async def observation(session, child, writer) -> uuid.UUID:
    row = ObservationFood(
        child_id=child.id,
        source_writer=writer.id,
        raw_text="계란말이를 다 먹었다",
        subject="계란말이",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(date(2026, 10, 13), date(2026, 10, 14)),
    )
    session.add(row)
    await session.flush()
    return row.id


def payload(*, kind="general", citations=(), content="계란말이") -> dict:
    return {
        "agent": "food",
        "kind": kind,
        "content": content,
        "reason": "이유",
        "citations": [dict(item) for item in citations],
        "allergens": ["난류"],
        "items": ["계란"],
    }


def cited(kind: str, source_id: uuid.UUID, note: str = "다 먹었다") -> dict:
    return {"source_kind": kind, "source_id": str(source_id), "note": note}


async def counts(session, child_id) -> tuple[int, int]:
    """그 아이의 추천 행 수 · 근거 행 수. 개발 DB 의 다른 행을 세지 않게 아이로 거른다."""
    suggestions = await session.scalar(
        select(func.count()).select_from(Suggestion).where(Suggestion.child_id == child_id)
    )
    evidence = await session.scalar(
        select(func.count())
        .select_from(SuggestionEvidence)
        .join(Suggestion, Suggestion.id == SuggestionEvidence.suggestion_id)
        .where(Suggestion.child_id == child_id)
    )
    return suggestions, evidence


def test_근거_종류_목록이_Agent_쪽과_같다():
    """domains 는 agents 를 import 하지 않아서 목록이 두 곳에 있다. 한쪽만 늘리면 새 근거
    종류가 저장에서 거절되거나, 아이 기록이 문서 행처럼 검사 없이 들어간다."""
    assert repository._CHILD_RECORD_KINDS == frozenset(get_args(ChildRecordKind))
    assert repository._DOC_KINDS == frozenset(get_args(DocKind))


async def test_추천을_draft_로_저장하고_id_를_순서대로_돌려준다(session, kids):
    owner, child, _ = kids
    obs = await observation(session, child, owner)

    ids = await save_agent_suggestions(
        session,
        child_id=child.id,
        run_id=RUN,
        now=NOW,
        payloads=[
            payload(kind="personalized", citations=[cited("observation_food", obs)]),
            payload(content="두부조림"),
        ],
    )

    rows = [await session.get(Suggestion, row_id) for row_id in ids]
    assert [row.content for row in rows] == ["계란말이", "두부조림"]
    assert [row.kind for row in rows] == [SuggestionKind.PERSONALIZED, SuggestionKind.GENERAL]
    assert {row.status for row in rows} == {SuggestionStatus.DRAFT}
    assert {row.run_id for row in rows} == {RUN}
    assert {row.expires_at for row in rows} == {NOW + SUGGESTION_TTL}
    assert (rows[0].allergens, rows[0].items) == (["난류"], ["계란"])
    assert await counts(session, child.id) == (2, 1)


async def test_SuggestionDraft_의_payload_가_그대로_저장된다(session, kids):
    """러너는 `draft.to_payload()` 를 그대로 넘긴다. 두 쪽이 각자 가짜로만 검증되면 모양이
    어긋나도 모른다 — 어긋나면 저장이 전부 KeyError 로 실패하고 카드가 안 나간다."""
    owner, child, _ = kids
    obs = await observation(session, child, owner)
    draft = SuggestionDraft(
        agent="food",
        kind="personalized",
        content="계란말이",
        reason="자주 먹어서요",
        citations=(
            EvidenceCitation(ref=Ref(kind="observation_food", id=obs), note="다 먹었다"),
            EvidenceCitation(ref=Ref(kind="food_doc", id=uuid.uuid4()), note="질감 안내"),
        ),
        allergens=("난류",),
        items=("계란",),
    )

    [row_id] = await save_agent_suggestions(
        session, child_id=child.id, run_id=RUN, now=NOW, payloads=[draft.to_payload()]
    )

    row = await session.get(Suggestion, row_id)
    assert (row.kind, row.agent, row.reason) == (
        SuggestionKind.PERSONALIZED,
        "food",
        "자주 먹어서요",
    )
    assert (row.allergens, row.items) == (["난류"], ["계란"])
    assert await counts(session, child.id) == (1, 2)


async def test_문서_행_근거는_대상_테이블_없이_저장한다(session, kids):
    _, child, _ = kids
    doc = uuid.uuid4()

    [row_id] = await save_agent_suggestions(
        session,
        child_id=child.id,
        run_id=RUN,
        now=NOW,
        payloads=[payload(citations=[cited("food_doc", doc, "질감 안내")])],
    )

    row = await session.scalar(
        select(SuggestionEvidence).where(SuggestionEvidence.suggestion_id == row_id)
    )
    assert (row.source_kind, row.source_id, row.note) == ("food_doc", doc, "질감 안내")


async def test_같은_근거를_두_번_인용하면_첫_인용만_남는다(session, kids):
    owner, child, _ = kids
    obs = await observation(session, child, owner)
    twice = [cited("observation_food", obs, "첫 인용"), cited("observation_food", obs, "두 번째")]

    [row_id] = await save_agent_suggestions(
        session,
        child_id=child.id,
        run_id=RUN,
        now=NOW,
        payloads=[payload(kind="personalized", citations=twice)],
    )

    rows = await session.scalars(
        select(SuggestionEvidence).where(SuggestionEvidence.suggestion_id == row_id)
    )
    assert [row.note for row in rows] == ["첫 인용"]


@pytest.mark.parametrize(
    ("make", "why"),
    [
        (lambda obs, others: payload(kind="personalized"), "개인화인데 아이 기록 근거 0행"),
        (
            lambda obs, others: payload(
                kind="personalized", citations=[cited("food_doc", uuid.uuid4())]
            ),
            "문서 행만 단 개인화",
        ),
        (
            lambda obs, others: payload(citations=[cited("observation_food", obs)]),
            "일반인데 아이 기록 근거",
        ),
        (
            lambda obs, others: payload(
                kind="personalized", citations=[cited("observation_food", others)]
            ),
            "다른 아이의 관찰",
        ),
        (
            lambda obs, others: payload(
                kind="personalized", citations=[cited("observation_food", uuid.uuid4())]
            ),
            "없는 관찰",
        ),
        (
            lambda obs, others: payload(
                kind="personalized", citations=[cited("daycare_meal", uuid.uuid4())]
            ),
            "아직 확인할 수 없는 아이 기록",
        ),
        (
            lambda obs, others: payload(citations=[cited("menu_catalog", uuid.uuid4())]),
            "정해지지 않은 근거 종류",
        ),
    ],
)
async def test_근거가_어긋난_묶음은_한_행도_쓰지_않는다(session, kids, make, why):
    owner, child, other = kids
    obs = await observation(session, child, owner)
    others = await observation(session, other, owner)

    with pytest.raises(SuggestionEvidenceError):
        await save_agent_suggestions(
            session,
            child_id=child.id,
            run_id=RUN,
            now=NOW,
            payloads=[payload(), make(obs, others)],  # 앞 항목은 맞아도 묶음째 거절된다
        )

    assert await counts(session, child.id) == (0, 0), why
