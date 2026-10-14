"""05 진입점 (#249). Supervisor · Memory 없이 도메인 task 만, 05 요청 id 를 seed 로."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.agents import entrypoint, pipeline
from app.agents.activity.doc_seed import activity_doc_seed
from app.agents.food.doc_seed import food_doc_seed
from app.agents.suggestion_save import InMemorySuggestions

CHILD, PARENT = uuid.uuid4(), uuid.uuid4()


async def test_05_요청_id_로_돌고_처음_시각을_now_로_쓴다(monkeypatch):
    seen = {}

    async def fake_run(tasks, contexts, **kwargs):
        [task] = tasks
        seen["run_id"] = task.run_id
        seen["texts"] = task.request_texts
        seen["now"] = contexts["food"].now
        seen["save_run_id"] = kwargs["save_run_id"]
        return pipeline.SuggestRun(outcomes=(), failed=())

    monkeypatch.setattr(pipeline, "run_suggestions", fake_run)
    sent_at = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    await entrypoint.suggest(
        child_id=CHILD,
        parent_id=PARENT,
        tasks=[entrypoint.SuggestTask("food", "meal_recommendation", ("내일 아침 추천해줘",))],
        request_id="req-1",
        save_run_id="run-1",
        sent_at=sent_at,
        save_suggestions=InMemorySuggestions(),
    )
    assert seen == {
        "run_id": "req-1",
        "texts": ("내일 아침 추천해줘",),
        "now": sent_at,
        "save_run_id": "run-1",
    }


async def test_처음_시각이_없으면_지금을_쓴다(monkeypatch):
    seen = {}

    async def fake_run(tasks, contexts, **kwargs):
        seen["now"] = contexts["activity"].now
        return pipeline.SuggestRun(outcomes=(), failed=())

    monkeypatch.setattr(pipeline, "run_suggestions", fake_run)
    before = datetime.now(UTC)
    await entrypoint.suggest(
        child_id=CHILD,
        parent_id=PARENT,
        tasks=[entrypoint.SuggestTask("activity", None, ("오늘 뭐 하고 놀까",))],
        request_id="req-2",
        save_run_id=None,
        sent_at=None,
        save_suggestions=InMemorySuggestions(),
    )
    assert before <= seen["now"] <= datetime.now(UTC)


def test_추천_task_라벨():
    assert entrypoint.recommendation_task_type("food") == "meal_recommendation"
    assert entrypoint.recommendation_task_type("activity") is None
    assert entrypoint.SUGGESTION_AGENTS == {"food", "activity"}


def test_사전검사는_active_를_막고_없는_것만_묻는다():
    rows = [entrypoint.SafetyLabel("allergy", "우유", "active")]
    check = entrypoint.check_allergens(["우유", "땅콩"], rows)
    assert check == entrypoint.AllergenCheck(blocked=("우유",), unknown=("땅콩",))


def test_안내_문구는_코드_문구다():
    assert entrypoint.count_notice_text(3) is None
    assert entrypoint.count_notice_text(0).code == "empty"
    assert entrypoint.count_notice_text(2).body == "조건에 맞는 추천을 2개 준비했어요."
    assert entrypoint.cannot_resume_text().code == "cannot_resume"


def test_문서_행은_시드의_검수된_행에서_찾는다(monkeypatch):
    doc_id = uuid.uuid4()
    monkeypatch.setattr(
        entrypoint,
        "_doc_index",
        lambda: {
            ("activity_doc", doc_id): entrypoint.DocCard(
                "블록 쌓기", "교육부", "표준보육과정", None
            )
        },
    )
    cards = entrypoint.describe_docs([("activity_doc", doc_id), ("food_doc", uuid.uuid4())])
    assert list(cards) == [("activity_doc", doc_id)]


def test_검수된_시드_행은_Agent_가_근거로_단_id_로_찾아진다(monkeypatch):
    """Agent 의 [예시] 행 id 와 05 의 문서 조회 id 가 같은 식이어야 근거가 조용히 사라지지 않는다.

    시드 행은 아직 전부 draft 다 — 첫 행만 approved 로 바꿔 서비스 경로(approved 만)를 본다.
    """
    from app.agents.activity import doc_seed as activity_seed
    from app.agents.food import doc_seed as food_seed
    from app.agents.food.store import InMemoryFoodDocs

    activity_entry = activity_doc_seed()[0]
    food_entry = food_doc_seed()[0]
    activity_entry = replace(activity_entry, meta=replace(activity_entry.meta, status="approved"))
    food_entry = replace(food_entry, meta=replace(food_entry.meta, status="approved"))
    monkeypatch.setattr(activity_seed, "activity_doc_seed", lambda: (activity_entry,))
    monkeypatch.setattr(food_seed, "food_doc_seed", lambda: (food_entry,))
    entrypoint._doc_index.cache_clear()
    try:
        [food_row] = InMemoryFoodDocs.from_seed()._rows
        refs = [("activity_doc", activity_entry.to_row().id), ("food_doc", food_row.id)]
        cards = entrypoint.describe_docs(refs)
    finally:
        entrypoint._doc_index.cache_clear()

    assert set(cards) == set(refs)
    assert cards[refs[0]].title == activity_entry.meta.title
    assert cards[refs[1]].source_org == food_entry.meta.source_org


def test_검수_전_시드_행은_근거로_나가지_않는다():
    """draft 행은 Agent 도 [예시] 로 받지 않는다. 05 도 내용을 싣지 않는다."""
    draft = next((e for e in activity_doc_seed() if e.meta.status != "approved"), None)
    if draft is None:
        pytest.skip("검수 전 시드 행이 없다")
    assert entrypoint.describe_docs([("activity_doc", draft.to_row().id)]) == {}
