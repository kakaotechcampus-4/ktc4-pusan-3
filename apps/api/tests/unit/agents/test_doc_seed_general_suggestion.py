"""검수를 마친 문서 시드가 있어야 일반 추천이 열린다 (#434).

최상위 §2 — 일반 추천의 근거는 문서 행이다 (#249). 서비스는 `approved` 행만 읽으므로
그 행이 하나도 없으면 Agent 는 일반 추천을 만들지 못한다.

이 파일은 "시드 → Agent 의 [예시] → 일반 추천 → 05 의 문서 카드" 가 실제 시드 파일로
이어지는지를 본다. 행마다의 내용 검사는 각 도메인의 `*_doc_seed` 테스트가 한다.
"""

import pytest

from app.agents import entrypoint
from app.agents.activity.agent import run
from app.agents.activity.doc_seed import activity_doc_seed
from app.agents.activity.store.inmemory import InMemoryActivityDocs
from app.agents.activity.tools.docs import search_activity_doc
from app.agents.food.doc_seed import food_doc_seed
from app.agents.growth.doc_seed import growth_doc_seed
from tests.unit.agents.activity.test_activity_agent import (
    CHILD,
    FakeLLM,
    _tools,
    candidate,
    context,
    memory,
    task,
)

SEEDS = {
    "activity": activity_doc_seed,
    "food": food_doc_seed,
    "growth": growth_doc_seed,
}


@pytest.mark.parametrize("domain", sorted(SEEDS))
def test_도메인마다_검수를_마친_문서_행이_있다(domain: str) -> None:
    approved = [e for e in SEEDS[domain]() if e.meta.status == "approved"]

    assert approved, f"{domain}_doc.yaml 에 approved 행이 없다 — 서비스가 읽을 문서가 0행이다"
    for entry in approved:
        # approved 는 검수자 · 검수일이 있어야 읽힌다 (parse_doc_meta) — 작성자와 같은 사람은 막힌다
        assert entry.meta.reviewed_by and entry.meta.reviewed_at, entry.meta.doc_key
        assert entry.meta.reviewed_by != entry.meta.authored_by, entry.meta.doc_key


async def test_Activity_는_검수된_시드_행을_인용해_일반_추천을_낸다() -> None:
    docs = InMemoryActivityDocs.from_seed()  # 운영 포트(unwired_ports)와 같은 읽기
    ctx = context(docs=docs)
    shown = await search_activity_doc(
        docs,
        months=40,
        query=" ".join(task().request_texts),
        rotation=f"{CHILD}|{ctx.today.isoformat()}",
    )
    assert shown, "[예시] 로 보여 줄 검수된 행이 없다 — 모델이 인용할 문서 id 가 없다"
    assert len(shown) >= 3
    doc = shown[0]
    # 후보마다 그 활동의 [예시] 행을 단다 — 맞지 않는 행을 달면 거절된다(UNRELATED_DOC)
    proposals = [
        {
            **candidate(row.title),
            "evidence": [{"id": str(row.id), "note": "이 나이 예시를 참고했어요"}],
        }
        for row in shown[:3]
    ]
    llm = FakeLLM(memory(), _tools(("propose_activity_candidates", {"candidates": proposals})))

    result = await run(task(), ctx, client=llm)

    assert result.status == "completed"  # BUILD 거절 루프로 failed 가 되지 않는다
    assert {draft.kind for draft in result.suggestions} == {"general"}
    assert [(c.ref.kind, c.ref.id) for c in result.suggestions[0].citations] == [
        ("activity_doc", doc.id)
    ]


def test_05_는_검수된_시드_행의_출처를_카드로_싣는다() -> None:
    from app.agents.common.reference_docs import doc_row_id

    entrypoint._doc_index.cache_clear()
    refs = [
        (f"{kind}_doc", doc_row_id(f"{kind}_doc", e.meta.doc_key))
        for kind, seed in (("activity", activity_doc_seed), ("food", food_doc_seed))
        for e in seed()
        if e.meta.status == "approved"
    ]
    assert refs, "검수된 시드 행이 없다"

    cards = entrypoint.describe_docs(refs)

    assert set(cards) == set(refs)  # 근거로 달린 행이 05 에서 조용히 사라지지 않는다
    assert all(card.source_org and card.source_title for card in cards.values())
