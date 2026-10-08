"""`search_growth_doc` — run 시작에 코드가 부르는 문서 조회 (Tool_명세 §5-4 · test_expected §12)

지키는 것 네 가지.
- 쿼리는 코드가 조립한다 — 라벨 · 단계 · 관심사 `merge_key` · 루틴 카테고리만. 보호자 발화 0건
- 월령 밖 행은 포트가 걸러 주지 않아도 나가지 않는다 (tool 게이트 다음의 두 번째 관문)
- 결과는 참고다 — `growth_doc` 로만 싣고 개인화 근거로 세지 않는다
- 라벨 · 월령 · 카테고리가 어느 row_type 을 읽을지 정한다. 모델이 고르지 않는다
"""

import inspect
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.agents.common.refs import count_child_records
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemoryGrowthDocs
from app.agents.growth.store.ports import GrowthDocRow, GrowthDocType
from app.agents.growth.tools.docs import DOC_LIMIT, doc_row_type, search_growth_doc
from app.rules.age import stage_of

LEARNING = GrowthTaskType.LEARNING_SUGGESTION
ROUTINE = GrowthTaskType.ROUTINE_COACHING


def doc_row(
    key: str, row_type: GrowthDocType = "learning_activity", lo: int = 12, hi: int = 36, **kw
) -> GrowthDocRow:
    return GrowthDocRow(
        id=uuid5(NAMESPACE_URL, key),
        doc_key=key,
        row_type=row_type,
        title=key,
        body="본문",
        min_month=lo,
        max_month=hi,
        **kw,
    )


class SpyDocs:
    """걸러 주지 않는 포트 — 월령 · 종류 필터를 tool 이 한 번 더 거는지 본다."""

    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.calls: list[dict] = []

    async def search(self, **kwargs) -> list[GrowthDocRow]:
        self.calls.append(kwargs)
        return list(self.rows)


class TestQuery:
    async def test_포트에는_한_번_간다(self):
        spy = SpyDocs()
        await search_growth_doc(spy, task_type=LEARNING, months=20)
        assert len(spy.calls) == 1

    async def test_쿼리는_라벨_단계_관심사_카테고리로만_조립된다(self):
        spy = SpyDocs()
        await search_growth_doc(
            spy,
            task_type=ROUTINE,
            months=30,
            interest_keys=("칫솔질", "양치"),
            routine_category="self_care",
        )
        assert spy.calls[0]["query"].split() == [
            "routine_coaching",
            stage_of(30),
            "칫솔질",
            "양치",
            "self_care",
        ]

    async def test_관심사와_카테고리가_없으면_라벨과_단계만_남는다(self):
        spy = SpyDocs()
        await search_growth_doc(spy, task_type=LEARNING, months=20)
        assert spy.calls[0]["query"].split() == ["learning_suggestion", stage_of(20)]

    def test_보호자_발화를_넣을_칸이_인자에_없다(self):
        """자유 문장을 받는 인자가 생기면 이 목록을 일부러 고치게 된다."""
        assert set(inspect.signature(search_growth_doc).parameters) == {
            "docs",
            "task_type",
            "months",
            "interest_keys",
            "routine_category",
            "trigger_tags",
        }

    async def test_trigger_tags_는_영문_식별자만_받는다(self):
        """tag 자리에 발화 한 줄이 들어와 필터 · 쿼리로 흘러가지 않게 한다."""
        spy = SpyDocs()
        with pytest.raises(ValueError, match="trigger_tags"):
            await search_growth_doc(
                spy,
                task_type=ROUTINE,
                months=40,
                routine_category="habit",
                trigger_tags=("방금 소리를 지르며 던졌어요",),
            )
        assert spy.calls == []

    async def test_trigger_tags_는_그대로_포트로_간다(self):
        spy = SpyDocs()
        await search_growth_doc(
            spy,
            task_type=ROUTINE,
            months=40,
            routine_category="habit",
            trigger_tags=("bored", "tired"),
        )
        assert spy.calls[0]["trigger_tags"] == ("bored", "tired")

    async def test_모르는_루틴_카테고리는_실패한다(self):
        spy = SpyDocs()
        with pytest.raises(ValueError, match="routine_category"):
            await search_growth_doc(spy, task_type=ROUTINE, months=30, routine_category="sleep")
        assert spy.calls == []


class TestSecondGate:
    async def test_월령_밖_행은_포트가_돌려줘도_나가지_않는다(self):
        """C12 + 36개월 이상 전용 행만 있음 → 빈 결과 → 호출부가 doc.no_row 로 간다."""
        spy = SpyDocs([doc_row("growth.learn.nuri", lo=36, hi=72)])
        assert await search_growth_doc(spy, task_type=LEARNING, months=12) == ()

    @pytest.mark.parametrize(("months", "served"), [(23, True), (24, False)])
    async def test_월령은_max_month_미만까지다(self, months, served):
        spy = SpyDocs([doc_row("growth.learn.a", lo=12, hi=24)])
        assert bool(await search_growth_doc(spy, task_type=LEARNING, months=months)) is served

    async def test_다른_종류_행은_나가지_않는다(self):
        spy = SpyDocs([doc_row("growth.routine.x", row_type="routine_step")])
        assert await search_growth_doc(spy, task_type=LEARNING, months=20) == ()

    async def test_35개월은_표준보육과정_행_36개월은_누리과정_행을_집는다(self):
        docs = InMemoryGrowthDocs(
            [
                doc_row("growth.learn.standard", lo=12, hi=36),
                doc_row("growth.learn.nuri", lo=36, hi=72),
            ]
        )
        at_35 = await search_growth_doc(docs, task_type=LEARNING, months=35)
        at_36 = await search_growth_doc(docs, task_type=LEARNING, months=36)
        assert [h.row.doc_key for h in at_35] == ["growth.learn.standard"]
        assert [h.row.doc_key for h in at_36] == ["growth.learn.nuri"]


class TestResult:
    async def test_최대_세_행만_온다(self):
        rows = [doc_row(f"growth.learn.{i}") for i in range(5)]
        spy = SpyDocs(rows)
        hits = await search_growth_doc(spy, task_type=LEARNING, months=20)
        assert DOC_LIMIT == 3
        assert spy.calls[0]["limit"] == DOC_LIMIT
        assert [h.row.doc_key for h in hits] == [
            "growth.learn.0",
            "growth.learn.1",
            "growth.learn.2",
        ]

    async def test_결과는_growth_doc_참고로만_싣는다(self):
        spy = SpyDocs([doc_row("growth.learn.a")])
        hits = await search_growth_doc(spy, task_type=LEARNING, months=20)
        refs = tuple(h.ref for h in hits)
        assert [r.kind for r in refs] == ["growth_doc"]
        assert refs[0].id == hits[0].row.id
        assert count_child_records(refs) == 0  # 문서 행만으로는 개인화 근거가 0건이다

    async def test_결과가_없으면_빈_튜플이다(self):
        assert await search_growth_doc(SpyDocs(), task_type=LEARNING, months=20) == ()


class TestRowType:
    @pytest.mark.parametrize(
        ("task_type", "months", "category", "expected"),
        [
            (LEARNING, 12, None, "learning_activity"),
            (LEARNING, 60, None, "learning_activity"),
            (ROUTINE, 6, "self_care", "rhythm_info"),
            (ROUTINE, 11, None, "rhythm_info"),
            (ROUTINE, 12, "self_care", "routine_step"),
            (ROUTINE, 30, "mealtime", "routine_step"),
            (ROUTINE, 30, "transition", "routine_step"),
            (ROUTINE, 30, None, "routine_step"),
            (ROUTINE, 30, "social_manner", "manner_practice"),
            (ROUTINE, 40, "habit", "habit_strategy"),
            (GrowthTaskType.BOOK_SUGGESTION, 20, None, "book_guide"),
            (GrowthTaskType.GROWTH_REVIEW, 20, None, "measure_guide"),
        ],
    )
    def test_라벨_월령_카테고리가_종류를_정한다(self, task_type, months, category, expected):
        assert doc_row_type(task_type, months, category) == expected

    async def test_포트에_넘기는_종류는_코드가_정한_값이다(self):
        spy = SpyDocs()
        await search_growth_doc(spy, task_type=ROUTINE, months=30, routine_category="social_manner")
        assert spy.calls[0]["row_type"] == "manner_practice"
        assert spy.calls[0]["routine_category"] == "social_manner"
        assert spy.calls[0]["months"] == 30
