"""affinity 0행(Curator 가 아직 확정한 관심이 없는 운영 상태)에서 근거를 고르는 법 (G4).

운영 Memory store 에는 profile_affinity 가 없어 `search_affinity` 가 늘 0행이다(memory_bridge).
그때 교육은 14일 안 관찰이 있으면 티어 3 개인화, 없으면 [예시] 문서 행을 근거로 한 일반 추천이다.
6개월 넘은 관심만 인용하면 그 인용은 빠지고 일반 추천이다(루트 §2). 일반 추천이 아이 기록 근거를
달고 나가면 개인화로 집계된다 — 0행이어야 한다.
"""

from datetime import timedelta
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.agents.growth.agent import run
from app.agents.growth.review import GENERAL_REASON
from app.agents.growth.store.inmemory import InMemoryGrowthMemory
from app.agents.growth.store.ports import EducationObservation
from tests.unit.agents.growth.support import CHILD, TODAY, task
from tests.unit.agents.growth.test_growth_agent import FakeLLM, call, docs_ctx, example_id, learning

REQUEST = "숫자 놀이 뭐 없어?"
EDU = EducationObservation(
    id=UUID(int=3001),
    child_id=CHILD,
    observed_on=TODAY - timedelta(days=3),
    subject="숫자",
    topic="계단 숫자 세기",
    polarity=1,
)
OLD = AffinityRow(
    id=UUID(int=3002),
    merge_key="공룡",
    state="confirmed",
    polarity=1,
    strength=0.9,
    last_observed_on=TODAY - timedelta(days=200),
    observed_days=3,
)


def item(title: str, *evidence: dict, reason: str = "이 나이에 많이 하는 활동이에요") -> dict:
    return {
        "title": title,
        "steps": ["하나씩 해 본다"],
        "materials": [],
        "evidence": list(evidence),
        "reason": reason,
    }


def doc(doc_id: str) -> dict:
    return {"id": doc_id, "note": "이 나이 예시를 참고했어요"}


async def test_관심도_기록도_없으면_문서_행을_근거로_한_일반_추천이다():
    doc_id = await example_id()
    llm = FakeLLM(
        call("search_affinity", {"domains": ["education", "activity"]}),
        call("search_education_memory", {"keywords": []}),
        learning("블록 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id),
    )
    result = await run(task("learning_suggestion", REQUEST), docs_ctx(), client=llm)
    assert result.status == "completed" and len(result.suggestions) == 3
    for draft in result.suggestions:
        assert draft.kind == "general" and draft.reason == GENERAL_REASON
        assert {c.ref.kind for c in draft.citations} == {"growth_doc"}
        assert not any(c.ref.is_child_record for c in draft.citations)


async def test_관심이_0행이어도_14일_안_교육_기록이_있으면_티어_3_개인화다():
    doc_id = await example_id()
    ctx = docs_ctx(memory=InMemoryGrowthMemory(education=[EDU]))
    llm = FakeLLM(
        call("search_affinity", {"domains": ["education"]}),
        call("search_education_memory", {"keywords": ["숫자"]}),
        call(
            "propose_learning_activity",
            {
                "items": [
                    item(
                        "계단 숫자 세기",
                        {"id": str(EDU.id), "note": "계단에서 숫자를 센 기록이 있어요"},
                        reason="최근 계단에서 숫자를 센 기록이 있어서 이어 가요",
                    ),
                    item("색깔 찾기", doc(doc_id)),
                    item("모양 맞추기", doc(doc_id)),
                ]
            },
        ),
    )
    result = await run(task("learning_suggestion", REQUEST), ctx, client=llm)
    assert ctx.state.seen_evidence[EDU.id].tier == 3
    personal = [d for d in result.suggestions if d.kind == "personalized"]
    assert len(personal) == 1
    assert [c.ref.kind for c in personal[0].citations] == ["observation_education"]


async def test_6개월_넘은_관심을_인용해도_일반_추천이고_그_인용은_빠진다():
    doc_id = await example_id()
    ctx = docs_ctx(memory=InMemoryGrowthMemory(affinities=[("education", OLD)]))
    llm = FakeLLM(
        call("search_affinity", {"domains": ["education"]}),
        call(
            "propose_learning_activity",
            {
                "items": [
                    item(
                        "공룡 숫자 세기",
                        {"id": str(OLD.id), "note": "공룡에 관심을 보인 적이 있어요"},
                        doc(doc_id),
                        reason="공룡 관심을 이어 가요",
                    ),
                    item("색깔 찾기", doc(doc_id)),
                    item("모양 맞추기", doc(doc_id)),
                ]
            },
        ),
    )
    result = await run(task("learning_suggestion", REQUEST), ctx, client=llm)
    first = result.suggestions[0]
    assert first.kind == "general" and first.reason == GENERAL_REASON
    assert [c.ref.kind for c in first.citations] == ["growth_doc"]
