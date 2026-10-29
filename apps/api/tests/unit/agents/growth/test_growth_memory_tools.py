"""Growth 조회 tool — 근거 순서 · seen 표 · 루틴 다음 단계를 코드가 고른다."""

from datetime import timedelta
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.agents.growth.context import build_gate
from app.agents.growth.schemas.memory import (
    SearchAffinityArgs,
    SearchEducationMemoryArgs,
    SearchRoutineMemoryArgs,
)
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemoryGrowthDocs, InMemoryGrowthMemory
from app.agents.growth.store.ports import EducationObservation, RoutineObservation
from app.agents.growth.tools.memory import (
    search_affinity,
    search_education_memory,
    search_routine_memory,
)
from tests.unit.agents.growth.support import CHILD, TODAY, birth_for, context


def routine(n: int, subject: str, level: str | None, *, days_ago: int = 1, category="self_care"):
    return RoutineObservation(
        id=UUID(int=700 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        routine_category=category,
        polarity=0,
        assistance_level=level,
    )


async def opened(task_type: GrowthTaskType, **kwargs):
    ctx = context(birth_for(30), docs=InMemoryGrowthDocs.from_seed(), **kwargs)
    ctx.state.gate = await build_gate(ctx, task_type)
    return ctx


def education(n: int, *, days_ago: int) -> EducationObservation:
    return EducationObservation(
        id=UUID(int=800 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject="숫자",
        topic="계단 숫자 세기",
        polarity=1,
    )


class TestEducation:
    async def test_돌려준_근거만_seen_에_적는다(self):
        obs = education(1, days_ago=2)
        ctx = await opened(
            GrowthTaskType.LEARNING_SUGGESTION, memory=InMemoryGrowthMemory(education=[obs])
        )
        result = await search_education_memory(ctx, SearchEducationMemoryArgs())
        assert result.success
        assert [r["id"] for r in result.data["records"]] == [str(obs.id)]
        assert set(ctx.state.seen_evidence) == {obs.id}

    async def test_기록이_없으면_NO_RECORDS_다(self):
        ctx = await opened(GrowthTaskType.LEARNING_SUGGESTION)
        result = await search_education_memory(ctx, SearchEducationMemoryArgs())
        assert not result.success and result.error["code"] == "NO_RECORDS"

    async def test_14일_넘은_관찰은_나오지_않는다(self):
        old = education(2, days_ago=20)
        ctx = await opened(
            GrowthTaskType.LEARNING_SUGGESTION, memory=InMemoryGrowthMemory(education=[old])
        )
        result = await search_education_memory(ctx, SearchEducationMemoryArgs())
        assert not result.success


class TestAffinity:
    async def test_고른_도메인만_읽는다(self):
        row = AffinityRow(
            id=UUID(int=901),
            merge_key="공룡",
            state="confirmed",
            polarity=1,
            strength=0.9,
            last_observed_on=TODAY,
            observations=3,
        )
        memory = InMemoryGrowthMemory(affinities=[("activity", row)])
        ctx = await opened(GrowthTaskType.LEARNING_SUGGESTION, memory=memory)
        education_only = await search_affinity(ctx, SearchAffinityArgs(domains=["education"]))
        both = await search_affinity(ctx, SearchAffinityArgs(domains=["education", "activity"]))
        assert not education_only.success
        assert both.success and row.id in ctx.state.seen_evidence


class TestRoutine:
    async def test_도움_수준으로_사슬의_바로_다음_칸을_고른다(self):
        memory = InMemoryGrowthMemory(routine=[routine(1, "양치하기", "full_assist")])
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING, memory=memory)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        (step,) = result.data["next_steps"]
        (picked_step,) = ctx.state.steps.values()
        picked = picked_step.step.row
        assert step["subject"] == "양치하기"
        assert step["step_id"] == str(picked.id)
        assert "assistance:partial_assist" in picked.tags

    async def test_맞는_사슬이_없으면_단계_없이_doc_no_row_로_남긴다(self):
        memory = InMemoryGrowthMemory(routine=[routine(1, "신발 신기", "partial_assist")])
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING, memory=memory)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.data["next_steps"] == []
        assert ctx.state.steps["self_care|신발신기"].step.readout_key == "doc.no_row"

    async def test_도움_수준이_비면_되묻기로_남긴다(self):
        memory = InMemoryGrowthMemory(routine=[routine(1, "손 씻기", None)])
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING, memory=memory)
        await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        (picked,) = ctx.state.steps.values()
        assert picked.step.readout_key == "ask.routine_current"

    async def test_습관_카테고리는_사슬을_찾지_않는다(self):
        memory = InMemoryGrowthMemory(routine=[routine(1, "손톱 물기", None, category="habit")])
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING, memory=memory)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.data["next_steps"] == [] and ctx.state.steps == {}
        assert set(ctx.state.seen_routine) == {UUID(int=701)}

    async def test_기록이_없으면_NO_RECORDS_고_되묻기를_안내한다(self):
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.error["code"] == "NO_RECORDS"
        assert "items 를 비우고" in result.error["message"]

    async def test_같은_행동을_다르게_적어도_가장_최근_도움_수준으로_고른다(self):
        memory = InMemoryGrowthMemory(
            routine=[
                routine(1, "양치", "independent", days_ago=1),
                routine(2, "양치하기", "full_assist", days_ago=5),
            ]
        )
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING, memory=memory)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        (picked,) = ctx.state.steps.values()
        assert picked.step.readout_key == "next_step.chain_end"
        assert result.data["next_steps"] == []
