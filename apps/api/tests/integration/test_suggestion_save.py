"""Agent 결과 하나의 추천 저장 — 근거 검증 · 묶음 단위 · run_id · draft +24h."""

import uuid
from datetime import UTC, date, datetime
from typing import get_args

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range

from app.agents.common.refs import ChildRecordKind, DocKind, EvidenceCitation, MenuKind, Ref
from app.agents.common.suggestion import SuggestionDraft
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.food import repository as food_repository
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
from app.rules.menu_evidence import menu_evidence_id

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


def payload(
    *, kind="general", citations=(), content="계란말이", requested=None, agent="food"
) -> dict:
    item = {
        "agent": agent,
        "kind": kind,
        "content": content,
        "reason": "이유",
        "citations": [dict(item) for item in citations],
        "allergens": ["난류"],
        "items": ["계란"],
    }
    if requested is not None:
        item["requested"] = requested
    return item


def cited(kind: str, source_id: uuid.UUID, note: str = "다 먹었다") -> dict:
    return {"source_kind": kind, "source_id": str(source_id), "note": note}


DOC_ID = uuid.UUID("00000000-0000-5000-8000-000000000001")


def doc_citation(kind: str = "activity_doc") -> dict:
    """일반 추천의 근거는 문서 행이다 (루트 §2)."""
    return cited(kind, DOC_ID, "또래 놀이 자료")


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
            payload(content="두부조림", citations=[doc_citation()]),
        ],
    )

    rows = [await session.get(Suggestion, row_id) for row_id in ids]
    assert [row.content for row in rows] == ["계란말이", "두부조림"]
    assert [row.kind for row in rows] == [SuggestionKind.PERSONALIZED, SuggestionKind.GENERAL]
    assert {row.status for row in rows} == {SuggestionStatus.DRAFT}
    assert {row.run_id for row in rows} == {RUN}
    assert {row.expires_at for row in rows} == {NOW + SUGGESTION_TTL}
    assert (rows[0].allergens, rows[0].items) == (["난류"], ["계란"])
    assert {row.place_notice for row in rows} == {None}  # payload 에 키가 없으면 비운다
    assert [row.warnings for row in rows] == [[], []]
    assert await counts(session, child.id) == (2, 2)  # 아이 기록 근거 1행 + 문서 근거 1행


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


async def test_장소가_붙은_추천의_확인_문구를_저장한다(session, kids):
    """Activity 가 장소를 붙이면 코드 문구를 칸으로 싣는다 (#516). 근거 행은 늘지 않는다."""
    _, child, _ = kids
    notice = "공공데이터 기준 2026년 6월이에요. 가기 전에 운영 여부를 확인해 주세요."
    draft = SuggestionDraft(
        agent="activity",
        kind="general",
        content="사직어린이공원에서 모래성 쌓기",
        reason="이유",
        citations=(EvidenceCitation(ref=Ref(kind="activity_doc", id=DOC_ID), note="놀이 자료"),),
        place_notice=notice,
    )

    [row_id] = await save_agent_suggestions(
        session, child_id=child.id, run_id=RUN, now=NOW, payloads=[draft.to_payload()]
    )

    row = await session.get(Suggestion, row_id)
    assert row.place_notice == notice
    assert await counts(session, child.id) == (1, 1)  # 장소는 근거가 아니다


async def test_추천의_위험_경고를_저장한다(session, kids):
    """위험 용어 사전의 축 문구를 그 추천의 칸으로 싣는다 (#402). 근거 행은 늘지 않는다."""
    _, child, _ = kids
    warning = "작은 부품은 삼킬 수 있어요. 아이 입에 들어가지 않는 크기인지 먼저 확인해 주세요."
    draft = SuggestionDraft(
        agent="activity",
        kind="general",
        content="구슬 꿰기",
        reason="이유",
        citations=(EvidenceCitation(ref=Ref(kind="activity_doc", id=DOC_ID), note="놀이 자료"),),
        warnings=(warning,),
    )

    [row_id] = await save_agent_suggestions(
        session, child_id=child.id, run_id=RUN, now=NOW, payloads=[draft.to_payload()]
    )

    row = await session.get(Suggestion, row_id)
    assert row.warnings == [warning]
    assert await counts(session, child.id) == (1, 1)  # 경고는 근거가 아니다


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
            # 앞 항목은 맞아도 묶음째 거절된다
            payloads=[payload(citations=[doc_citation()]), make(obs, others)],
        )

    assert await counts(session, child.id) == (0, 0), why


async def test_보호자가_정한_요청에_맞춘_일반_추천은_문서_근거_없이도_쓴다(session, kids):
    """ "물놀이 뭐 하지?" 처럼 놀이를 정해 물었으면 그 요청이 기준이다 (루트 §2)."""
    _, child, _ = kids
    [suggestion_id] = await save_agent_suggestions(
        session,
        child_id=child.id,
        run_id=RUN,
        payloads=[payload(kind="general", requested=True, agent="activity")],
        now=NOW,
    )
    assert suggestion_id
    assert await counts(session, child.id) == (1, 0)


async def test_요청_기반_면제는_Activity_만이다(session, kids):
    """Food 는 레시피북 밖 메뉴가 나가면 안 된다 — requested 가 와도 문서 근거를 본다."""
    _, child, _ = kids
    with pytest.raises(SuggestionEvidenceError, match="문서 행 근거가 없다"):
        await save_agent_suggestions(
            session,
            child_id=child.id,
            run_id=RUN,
            payloads=[payload(kind="general", requested=True, agent="food")],
            now=NOW,
        )
    assert await counts(session, child.id) == (0, 0)


async def test_요청_기반이어도_아이_기록_근거가_있으면_일반이_아니다(session, kids):
    """요청 기반은 종류가 아니다 — 기록을 인용했으면 개인화로 내야 한다."""
    owner, child, _ = kids
    obs = await observation(session, child, owner)
    with pytest.raises(SuggestionEvidenceError, match="아이 기록 근거가 있다"):
        await save_agent_suggestions(
            session,
            child_id=child.id,
            run_id=RUN,
            payloads=[
                payload(
                    kind="general",
                    requested=True,
                    agent="activity",
                    citations=[cited("observation_food", obs)],
                )
            ],
            now=NOW,
        )


async def test_일반_추천인데_문서_근거가_없으면_한_행도_쓰지_않는다(session, kids):
    """일반 추천의 근거는 문서 행이다 (루트 §2). 개인화 0행과 같은 자리에서 막는다."""
    _, child, _ = kids
    with pytest.raises(SuggestionEvidenceError, match="문서 행 근거"):
        await save_agent_suggestions(
            session, child_id=child.id, run_id=RUN, payloads=[payload(kind="general")], now=NOW
        )
    assert await counts(session, child.id) == (0, 0)


# ── Food 일반 추천의 메뉴 근거 (#442) ────────────────────────────────────
async def recipe_in_catalog(session, source_id="7", *, resolved=True) -> uuid.UUID:
    """레시피 카탈로그에 한 행을 넣고 그 행을 가리키는 메뉴 근거 id 를 돌려준다."""
    await food_repository.upsert_recipes(
        session,
        rows=[
            {
                "source": "mafra_recipe",
                "source_id": source_id,
                "menu_key": f"레시피{source_id}",
                "display_name": f"레시피{source_id}",
                "ingredients": ["두부"],
                "resolved": resolved,
                "synced_at": NOW,
            }
        ],
    )
    return menu_evidence_id("mafra_recipe", source_id)


def menu_citation(menu: uuid.UUID) -> dict:
    return cited("recipe_catalog", menu, "재료가 검수된 메뉴예요.")


def test_메뉴_근거_종류_목록이_Agent_쪽과_같다():
    assert repository._MENU_KINDS == frozenset(get_args(MenuKind))


async def test_Food_일반_추천은_메뉴_근거만으로_저장한다(session, kids):
    """문서 행이 없어도 고른 메뉴(레시피 카탈로그 행)가 근거다 — 근거 0행이 아니다."""
    _, child, _ = kids
    menu = await recipe_in_catalog(session)

    [row_id] = await save_agent_suggestions(
        session,
        child_id=child.id,
        run_id=RUN,
        now=NOW,
        payloads=[payload(citations=[menu_citation(menu)])],
    )

    row = await session.scalar(
        select(SuggestionEvidence).where(SuggestionEvidence.suggestion_id == row_id)
    )
    assert (row.source_kind, row.source_id) == ("recipe_catalog", menu)


async def test_메뉴_근거는_Food_가_아니면_거절한다(session, kids):
    _, child, _ = kids
    menu = await recipe_in_catalog(session)

    with pytest.raises(SuggestionEvidenceError):
        await save_agent_suggestions(
            session,
            child_id=child.id,
            run_id=RUN,
            now=NOW,
            payloads=[payload(citations=[menu_citation(menu)]) | {"agent": "activity"}],
        )
    assert await counts(session, child.id) == (0, 0)


async def test_메뉴_근거는_개인화_추천에_달지_않는다(session, kids):
    owner, child, _ = kids
    obs = await observation(session, child, owner)
    menu = await recipe_in_catalog(session)
    citations = [cited("observation_food", obs), menu_citation(menu)]

    with pytest.raises(SuggestionEvidenceError):
        await save_agent_suggestions(
            session,
            child_id=child.id,
            run_id=RUN,
            now=NOW,
            payloads=[payload(kind="personalized", citations=citations)],
        )


@pytest.mark.parametrize("which", ["unknown", "unresolved"])
async def test_카탈로그에_없거나_풀리지_않은_메뉴는_근거로_받지_않는다(session, kids, which):
    """근거가 실제 후보 풀의 레시피여야 한다 — 아니면 근거 0행과 같다."""
    _, child, _ = kids
    menu = (
        uuid.uuid4()
        if which == "unknown"
        else await recipe_in_catalog(session, "8", resolved=False)
    )

    with pytest.raises(SuggestionEvidenceError):
        await save_agent_suggestions(
            session,
            child_id=child.id,
            run_id=RUN,
            now=NOW,
            payloads=[payload(citations=[menu_citation(menu)])],
        )
    assert await counts(session, child.id) == (0, 0)
