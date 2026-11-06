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
    doc = shown[0]
    proposals = [
        {**candidate(name), "evidence": [{"id": str(doc.id), "note": "이 나이 예시를 참고했어요"}]}
        for name in ("종이컵 탑 쌓기", "그림자놀이", "색종이 접기")
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


@pytest.mark.parametrize("domain", sorted(SEEDS))
def test_일본_자료가_보류된_동안_pdl_1_행은_없다(domain: str) -> None:
    """`pdl_1` 은 값 계약에만 있다 (#510 Part F). 일본 가이드 원본이 404 라 행을 만들지 않는다."""
    rows = [e.meta.doc_key for e in SEEDS[domain]() if e.meta.license_basis == "pdl_1"]

    assert rows == []


def test_NHS_행의_이름은_Agent_입력과_05_카드_어디에도_나가지_않는다(monkeypatch) -> None:
    """실제 NHS 기관 · 제목 · 주소는 DocMeta(YAML)에만 남는다 (#510 Part C, NHS 약관 3.6(b)).

    `source_locator` 도 공개 칸이 아니다 — 모델 입력과 05 응답 어디에도 없다.
    """
    from dataclasses import fields
    from datetime import date

    from app.agents.common.reference_docs import doc_row_id
    from app.agents.food import doc_seed as food_seed
    from app.agents.food.doc_seed import parse_food_doc
    from app.agents.food.store import InMemoryFoodDocs

    locator = "(테스트) 내부 추적용 위치 7쪽"
    entries = parse_food_doc(
        {
            "rows": [
                {
                    "doc_key": "food.test.nhs",
                    "row_type": "feeding_tip",
                    "title": "(테스트) 행",
                    "body": "가짜 문장이다.",
                    "min_month": 24,
                    "max_month": 72,
                    "tags": ["test"],
                    "source_title": "(테스트) 가짜 NHS 문서 제목",
                    "source_org": "NHS (테스트)",
                    "source_year": 2025,
                    "source_locator": locator,
                    "source_url": "https://www.nhs.uk/test/fake-page/",
                    "license_basis": "ogl_3",
                    "status": "approved",
                    "authored_by": "writer-a",
                    "reviewed_by": "reviewer-b",
                    "reviewed_at": date(2026, 11, 3),
                    "version": 1,
                }
            ]
        }
    )
    monkeypatch.setattr(food_seed, "food_doc_seed", lambda: entries)
    entrypoint._doc_index.cache_clear()
    try:
        [row] = InMemoryFoodDocs.from_seed()._rows
        [card] = entrypoint.describe_docs(
            [("food_doc", doc_row_id("food_doc", "food.test.nhs"))]
        ).values()
    finally:
        entrypoint._doc_index.cache_clear()

    public = repr((row, card)).lower()
    for leaked in ("nhs", "nhs.uk", "가짜 nhs 문서 제목", locator.lower()):
        assert leaked not in public, leaked
    assert row.source_org == card.source_org == "영국 정부 공공 정보"
    assert "source_locator" not in {f.name for f in fields(row)} | {f.name for f in fields(card)}
    # 내부 추적 정보는 시드에 그대로 있다
    assert entries[0].meta.source_org == "NHS (테스트)"
    assert entries[0].meta.source_url == "https://www.nhs.uk/test/fake-page/"
