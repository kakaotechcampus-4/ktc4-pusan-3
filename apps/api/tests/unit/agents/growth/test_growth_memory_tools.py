"""Growth 조회 tool — 근거 순서 · seen 표 · 루틴 다음 단계를 코드가 고른다."""

import logging
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.common.evidence import AffinityRow
from app.agents.growth.context import build_gate
from app.agents.growth.doc_seed import growth_doc_seed
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
from tests.unit.agents.growth.support import (
    CALM,
    CHILD,
    GREETING,
    REST,
    TAKING_TURNS,
    TODAY,
    birth_for,
    context,
    fixture_docs,
    strategy_row,
)


def routine(
    n: int,
    subject: str,
    level: str | None,
    *,
    days_ago: int = 1,
    category="self_care",
    trigger: str | None = None,
):
    return RoutineObservation(
        id=UUID(int=700 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        routine_category=category,
        polarity=0,
        assistance_level=level,
        trigger=trigger,
    )


async def opened(task_type: GrowthTaskType, **kwargs):
    ctx = context(birth_for(30), docs=fixture_docs(), **kwargs)
    ctx.state.gate = await build_gate(ctx, task_type)
    return ctx


def education(
    n: int,
    *,
    days_ago: int,
    subject: str = "숫자",
    topic: str = "계단 숫자 세기",
    polarity: int = 1,
) -> EducationObservation:
    return EducationObservation(
        id=UUID(int=800 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject,
        topic=topic,
        polarity=polarity,
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

    async def test_국어로_글_읽기_기록을_찾는다(self):
        reading = education(
            1, days_ago=2, subject="글 읽기", topic="4문장 이상 글 읽기", polarity=-1
        )
        dino = education(2, days_ago=2, subject="공룡", topic="공룡 모형 줄 세우기")
        ctx = await opened(
            GrowthTaskType.LEARNING_SUGGESTION,
            memory=InMemoryGrowthMemory(education=[reading, dino]),
        )
        result = await search_education_memory(ctx, SearchEducationMemoryArgs(keywords=["국어"]))
        assert [r["id"] for r in result.data["records"]] == [str(reading.id)]

    async def test_영역_밖_낱말은_지금처럼_맞춘다(self):
        reading = education(1, days_ago=2, subject="글 읽기", topic="4문장 이상 글 읽기")
        dino = education(2, days_ago=2, subject="공룡", topic="공룡 모형 줄 세우기")
        ctx = await opened(
            GrowthTaskType.LEARNING_SUGGESTION,
            memory=InMemoryGrowthMemory(education=[reading, dino]),
        )
        result = await search_education_memory(ctx, SearchEducationMemoryArgs(keywords=["공룡"]))
        assert [r["id"] for r in result.data["records"]] == [str(dino.id)]


class TestAffinity:
    async def test_고른_도메인만_읽는다(self):
        row = AffinityRow(
            id=UUID(int=901),
            merge_key="공룡",
            state="confirmed",
            polarity=1,
            strength=0.9,
            last_observed_on=TODAY,
            observed_days=3,
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

    async def test_습관은_사슬을_찾지_않고_trigger_가_없으면_전략도_고르지_않는다(self):
        memory = InMemoryGrowthMemory(routine=[routine(1, "손톱 물기", None, category="habit")])
        ctx = await opened(GrowthTaskType.ROUTINE_COACHING, memory=memory)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.data["next_steps"] == []
        assert ctx.state.steps["habit|손톱물기"].step.readout_key == "doc.no_row"
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


class SpyDocs(InMemoryGrowthDocs):
    """포트가 받은 조회 인자를 남긴다 — 보호자가 말한 trigger 가 조회에 가지 않는지 본다."""

    def __init__(self, rows) -> None:
        super().__init__(rows)
        self.calls: list[dict] = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return await super().search(**kwargs)


async def strategy_ctx(months: int, observations, *rows):
    ctx = context(
        birth_for(months),
        docs=fixture_docs(*rows),
        memory=InMemoryGrowthMemory(routine=list(observations)),
    )
    ctx.state.gate = await build_gate(ctx, GrowthTaskType.ROUTINE_COACHING)
    return ctx


class TestStrategy:
    async def test_예절은_행동_이름이_맞는_연습_행을_고른다(self):
        obs = routine(1, "인사하기", None, category="social_manner")
        ctx = await strategy_ctx(40, [obs], GREETING, TAKING_TURNS)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        (step,) = result.data["next_steps"]
        assert (step["step_id"], step["subject"]) == (str(GREETING.id), "인사하기")

    async def test_예절에_맞는_행이_없으면_doc_no_row_로_남긴다(self):
        obs = routine(1, "인사하기", None, category="social_manner")
        ctx = await strategy_ctx(40, [obs], TAKING_TURNS)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.data["next_steps"] == []
        assert ctx.state.steps["social_manner|인사하기"].step.readout_key == "doc.no_row"

    async def test_습관은_trigger_의_상황_태그로_대응_행을_고른다(self):
        obs = routine(1, "손톱 물어뜯기", None, category="habit", trigger="긴장할 때")
        ctx = await strategy_ctx(40, [obs], CALM, REST)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        (step,) = result.data["next_steps"]
        assert (step["step_id"], step["subject"]) == (str(CALM.id), "손톱 물어뜯기")

    async def test_습관_trigger_가_사전에_없으면_doc_no_row_로_남긴다(self):
        obs = routine(1, "손톱 물어뜯기", None, category="habit", trigger="TV 볼 때")
        ctx = await strategy_ctx(40, [obs], CALM, REST)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.data["next_steps"] == []
        assert ctx.state.steps["habit|손톱물어뜯기"].step.readout_key == "doc.no_row"

    async def test_문서_조회에는_보호자가_말한_trigger_대신_코드가_정한_태그만_간다(self):
        obs = routine(1, "손톱 물어뜯기", None, category="habit", trigger="긴장할 때")
        docs = SpyDocs([CALM])
        ctx = context(birth_for(40), docs=docs, memory=InMemoryGrowthMemory(routine=[obs]))
        ctx.state.gate = await build_gate(ctx, GrowthTaskType.ROUTINE_COACHING)
        await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        (call,) = [c for c in docs.calls if c["row_type"] == "habit_strategy"]
        assert call["trigger_tags"] == ("anxious",)
        assert "긴장" not in call["query"]

    async def test_카테고리마다_전략_행은_세_칸까지다(self):
        rows = [
            strategy_row(f"growth.manner.greeting.r{n}", f"인사하기 — {n}", lo=24) for n in range(2)
        ] + [
            strategy_row(f"growth.manner.promise.r{n}", f"약속 지키기 — {n}", lo=24)
            for n in range(2)
        ]
        observations = [
            routine(1, "인사하기", None, category="social_manner"),
            routine(2, "약속 지키기", None, category="social_manner", days_ago=2),
        ]
        ctx = await strategy_ctx(40, observations, *rows)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert len(result.data["next_steps"]) == 3

    async def test_증상으로_닫히는_subject_는_전략_행을_받지_않고_doc_no_row_로_남긴다(self):
        """trigger 가 사전에 걸려도 눈 깜빡임 같은 증상은 습관 대응 행으로 가지 않는다."""
        obs = routine(1, "눈 깜빡임", None, category="habit", trigger="긴장할 때")
        ctx = await strategy_ctx(48, [obs], CALM, REST)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert result.data["next_steps"] == []
        assert ctx.state.steps["habit|눈깜빡임"].step.readout_key == "doc.no_row"

    async def test_습관은_행동마다_자기_trigger_로_행을_고른다(self):
        """손톱 물어뜯기 에 손가락 빨기 의 졸림 trigger 가 붙지 않는다."""
        observations = [
            routine(1, "손톱 물어뜯기", None, category="habit", days_ago=1),
            routine(2, "손가락 빨기", None, category="habit", days_ago=3, trigger="졸릴 때"),
        ]
        ctx = await strategy_ctx(40, observations, CALM, REST)
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert [(s["step_id"], s["subject"]) for s in result.data["next_steps"]] == [
            (str(REST.id), "손가락 빨기")
        ]
        assert ctx.state.steps["habit|손톱물어뜯기"].step.readout_key == "doc.no_row"

    async def test_다시_조회해도_카테고리의_전략_행은_세_칸을_넘지_않는다(self):
        rows = [
            strategy_row(f"growth.manner.{topic}.r{n}", f"{name} — {n}", lo=24)
            for topic, name in (("greeting", "인사하기"), ("promise", "약속 지키기"))
            for n in range(3)
        ]
        observations = [
            routine(1, "인사하기", None, category="social_manner"),
            routine(2, "약속 지키기", None, category="social_manner", days_ago=2),
        ]
        ctx = await strategy_ctx(40, observations, *rows)
        await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        result = await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        assert len(result.data["next_steps"]) == 3


_TOOTH = "growth.routine.self_care.toothbrush"


def _drop_step2(rows):
    return [row for row in rows if row.doc_key != f"{_TOOTH}.step2"]


def _untag_step3(rows):
    return [
        replace(row, tags=("self_care",)) if row.doc_key == f"{_TOOTH}.step3" else row
        for row in rows
    ]


class TestBrokenChain:
    """문서 검수에서 중간 단계가 draft 로 돌아가면 approved 사슬이 끊긴다 — run 이 죽지 않는다."""

    @pytest.mark.parametrize(
        "breaks", [_drop_step2, _untag_step3], ids=["중간_단계_빠짐", "도움_수준_태그_없음"]
    )
    async def test_끊긴_사슬은_그_행동만_doc_no_row_다(self, breaks, caplog):
        rows = breaks([e.to_row() for e in growth_doc_seed() if e.meta.status == "approved"])
        memory = InMemoryGrowthMemory(
            routine=[routine(1, "양치하기", "partial_assist"), routine(2, "손 씻기", "full_assist")]
        )
        ctx = context(birth_for(30), docs=InMemoryGrowthDocs(rows), memory=memory)
        ctx.state.gate = await build_gate(ctx, GrowthTaskType.ROUTINE_COACHING)
        with caplog.at_level(logging.WARNING):
            result = await search_routine_memory(ctx, SearchRoutineMemoryArgs(category="self_care"))
        assert result.success
        steps = {picked.subject: picked.step for picked in ctx.state.steps.values()}
        assert steps["양치하기"].readout_key == "doc.no_row"
        assert steps["손 씻기"].row is not None
        assert _TOOTH in caplog.text and "양치" not in caplog.text
