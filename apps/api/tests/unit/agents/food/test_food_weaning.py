"""`guide_weaning_stage` — 월령에 맞는 이유식 문서 행과 이미 먹어 본 재료.

시드(`food_doc.yaml`)를 그대로 읽는다. 시드 행은 아직 전부 draft 라 `include_draft=True` 로만
읽는다.
아이 · 기록은 전부 가짜다.
"""

from datetime import date

import pytest

from app.agents.food.doc_seed import food_doc_seed
from app.agents.food.schemas.infant import GuideWeaningStageArgs, WeaningTopic
from app.agents.food.store import InMemoryFoodDocs, InMemoryFoodMemory, SafetyEntry
from app.agents.food.tools.docs import DOC_LIMIT
from app.agents.food.tools.infant import guide_weaning_stage
from tests.unit.agents.food.support import CHILD, context, day, doc_row, observation

SIX_MONTHS = date(2026, 4, 10)  # NOW(2026-10-14) 기준 6개월
EIGHT_MONTHS = date(2026, 2, 10)  # 8개월
TWELVE_MONTHS = date(2025, 10, 10)  # 12개월 — 이유기를 지났다
EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]


def seed_docs() -> InMemoryFoodDocs:
    return InMemoryFoodDocs.from_seed(include_draft=True)


def titles_covering(months: int, row_type: str) -> set[str]:
    return {
        e.meta.title
        for e in food_doc_seed()
        if e.meta.row_type == row_type and e.meta.min_month <= months < e.meta.max_month
    }


async def test_6개월_아이에게는_6개월에_맞는_단계_행만_나온다() -> None:
    ctx = context(docs=seed_docs(), birth=SIX_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs(topic=WeaningTopic.TEXTURE))

    assert result.success
    docs = result.data["docs"]
    assert 0 < len(docs) <= DOC_LIMIT
    assert {d["title"] for d in docs} <= titles_covering(6, "weaning_stage")
    assert {d["row_type"] for d in docs} == {"weaning_stage"}


async def test_8개월_아이에게는_6개월_단계_행이_나오지_않는다() -> None:
    ctx = context(docs=seed_docs(), birth=EIGHT_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs(topic=WeaningTopic.TEXTURE))

    assert {d["title"] for d in result.data["docs"]} <= titles_covering(8, "weaning_stage")
    assert not {d["title"] for d in result.data["docs"]} & (
        titles_covering(6, "weaning_stage") - titles_covering(8, "weaning_stage")
    )


async def test_새_재료_주제는_재료_행만_가져온다() -> None:
    ctx = context(docs=seed_docs(), birth=EIGHT_MONTHS)

    result = await guide_weaning_stage(
        ctx, GuideWeaningStageArgs(topic=WeaningTopic.NEW_INGREDIENTS)
    )

    assert {d["row_type"] for d in result.data["docs"]} == {"weaning_ingredient"}


async def test_일반_주제는_단계와_재료_행을_다_본다() -> None:
    docs = InMemoryFoodDocs(
        [
            doc_row("질감", row_type="weaning_stage"),
            doc_row("재료", row_type="weaning_ingredient"),
            doc_row("식사 태도", row_type="feeding_tip"),
        ]
    )
    ctx = context(docs=docs, birth=EIGHT_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs(topic=WeaningTopic.GENERAL))

    assert {d["title"] for d in result.data["docs"]} == {"질감", "재료"}  # 이유기 행만이다


async def test_알레르기가_있는_아이에게_그_식품이_든_행은_나오지_않는다() -> None:
    ctx = context(docs=seed_docs(), birth=EIGHT_MONTHS, safety=EGG)

    result = await guide_weaning_stage(
        ctx, GuideWeaningStageArgs(topic=WeaningTopic.NEW_INGREDIENTS)
    )

    assert result.data["docs"]
    assert all("달걀" not in d["body"] and "계란" not in d["body"] for d in result.data["docs"])


async def test_결과_행마다_출처_기관이_있다() -> None:
    ctx = context(docs=seed_docs(), birth=SIX_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs())

    assert result.data["docs"]
    assert all(d["source_org"] for d in result.data["docs"])


async def test_결과에_실린_문서_행만_run_state_에_남는다() -> None:
    ctx = context(docs=seed_docs(), birth=SIX_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs())

    shown = {d["id"] for d in result.data["docs"]}
    assert {str(key) for key in ctx.state.seen_docs} == shown
    assert {d["kind"] for d in result.data["docs"]} == {"food_doc"}


async def test_참고_자료가_없으면_자료_없음으로_처리하고_지어내지_않는다() -> None:
    ctx = context(docs=InMemoryFoodDocs(), birth=SIX_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs())

    assert result.success
    assert result.data["docs"] == []
    assert result.data["reference"] == "자료 없음"
    assert not ctx.state.seen_docs


async def test_이유기가_지난_월령에는_자료_없음이다() -> None:
    ctx = context(docs=seed_docs(), birth=TWELVE_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs())

    assert result.data["docs"] == []
    assert result.data["reference"] == "자료 없음"


async def test_이미_먹어_본_재료를_붙이고_근거로_인용할_수_있게_남긴다() -> None:
    old = observation("고구마", days_ago=60)
    recent = observation("바나나", days_ago=2)
    memory = InMemoryFoodMemory({}, {CHILD: [old, recent, observation("고구마", days_ago=10)]})
    ctx = context(docs=seed_docs(), birth=SIX_MONTHS, memory=memory)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs())

    tried = {t["label"]: t for t in result.data["tried"]}
    assert set(tried) == {"고구마", "바나나"}  # 한 달 넘은 기록도 본다 — 도입한 재료를 세는 것이다
    assert tried["고구마"]["observed_on"] == day(10).isoformat()  # 같은 재료는 가장 최근 한 건
    assert tried["바나나"]["kind"] == "observation_food"
    assert {str(k) for k in ctx.state.seen_evidence} == {t["id"] for t in tried.values()}
    assert "raw_text" not in str(result.data)


async def test_기록이_없으면_먹어_본_재료_목록이_비어_있다() -> None:
    ctx = context(docs=seed_docs(), birth=SIX_MONTHS)

    result = await guide_weaning_stage(ctx, GuideWeaningStageArgs())

    assert result.data["tried"] == []


async def test_안전_정보를_못_읽었으면_문서를_조회하지_않는다() -> None:
    ctx = context(docs=seed_docs(), birth=SIX_MONTHS, safety=None)

    with pytest.raises(RuntimeError, match="안전 정보"):
        await guide_weaning_stage(ctx, GuideWeaningStageArgs())
