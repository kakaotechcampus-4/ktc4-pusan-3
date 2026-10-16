"""출력 tool — 안전 필터 → 금지 표현 → 근거 → build → 개수.

루틴은 닫힘 · 되묻기 · 단계 대조가 먼저다.
"""

from datetime import timedelta
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.agents.growth.context import build_gate
from app.agents.growth.schemas.learning import ProposeLearningActivityArgs
from app.agents.growth.schemas.memory import SearchAffinityArgs, SearchRoutineMemoryArgs
from app.agents.growth.schemas.routine import ProposeRoutinePlanArgs
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import (
    InMemoryGrowthDocs,
    InMemoryGrowthMemory,
    InMemorySafety,
)
from app.agents.growth.store.ports import RoutineObservation, SafetyEntry
from app.agents.growth.tools.memory import search_affinity, search_routine_memory
from app.agents.growth.tools.propose import propose_learning_activity, propose_routine_plan
from tests.unit.agents.growth.support import CHILD, TODAY, birth_for, context


def item(
    title: str,
    *,
    steps=("하나씩 늘어놓는다",),
    materials=(),
    evidence=(),
    reason="이 나이에 많이 하는 활동이에요",
):
    return {
        "title": title,
        "steps": list(steps),
        "materials": list(materials),
        "evidence": list(evidence),
        "reason": reason,
    }


async def learning_ctx(months=30, **kwargs):
    ctx = context(birth_for(months), docs=InMemoryGrowthDocs.from_seed(), **kwargs)
    ctx.state.gate = await build_gate(ctx, GrowthTaskType.LEARNING_SUGGESTION)
    rows = await ctx.ports.docs.search(
        months=months,
        row_type="learning_activity",
        routine_category=None,
        trigger_tags=(),
        query="",
        limit=3,
    )
    ctx.state.seen_docs = {row.id: row for row in rows}
    return ctx


def doc_pick(ctx) -> dict:
    return {"id": str(next(iter(ctx.state.seen_docs))), "note": "이 나이 예시를 참고했어요"}


def learning_args(ctx, *titles: str, **overrides) -> ProposeLearningActivityArgs:
    return ProposeLearningActivityArgs.model_validate(
        {"items": [item(t, evidence=[doc_pick(ctx)], **overrides) for t in titles]}
    )


class TestLearning:
    async def test_문서_행만_인용하면_일반_추천_세_개다(self):
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "블록 세기", "색깔 찾기", "모양 맞추기")
        )
        assert result.success
        assert [d.kind for d in ctx.state.suggestions] == ["general"] * 3
        assert ctx.state.suggestions[0].content.startswith("블록 세기\n1. ")

    async def test_음식_용어가_든_후보는_빠지고_사유를_알리지_않는다(self):
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "땅콩으로 숫자 세기", "색깔 찾기", "모양 맞추기")
        )
        assert result.success and len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("땅콩으로 숫자 세기",)
        assert "땅콩" not in str(result.to_payload())

    async def test_경고가_붙는_후보도_뺀다_결정A(self):
        ctx = await learning_ctx(months=40)
        await propose_learning_activity(
            ctx, learning_args(ctx, "욕조에서 물 붓기", "색깔 찾기", "모양 맞추기")
        )
        assert ctx.state.excluded == ("욕조에서 물 붓기",)

    async def test_평가_표현은_빼고_재호출_사유로_남긴다(self):
        ctx = await learning_ctx()
        args = ProposeLearningActivityArgs.model_validate(
            {
                "items": [
                    item(
                        "숫자 세기",
                        reason="또래보다 늦어서 연습이 필요해요",
                        evidence=[doc_pick(ctx)],
                    ),
                    item("색깔 찾기", evidence=[doc_pick(ctx)]),
                    item("모양 맞추기", evidence=[doc_pick(ctx)]),
                ]
            }
        )
        await propose_learning_activity(ctx, args)
        assert ctx.state.excluded == ("숫자 세기",)

    async def test_조회하지_않은_id_는_EVIDENCE_REQUIRED_로_거절한다(self):
        ctx = await learning_ctx()
        fake = {"id": str(UUID(int=12345)), "note": "지어낸 근거"}
        args = ProposeLearningActivityArgs.model_validate(
            {"items": [item(t, evidence=[fake]) for t in ("a 놀이", "b 놀이", "c 놀이")]}
        )
        result = await propose_learning_activity(ctx, args)
        assert not result.success and result.error["code"] == "EVIDENCE_REQUIRED"
        assert ctx.state.suggestions == ()

    async def test_근거가_하나도_없으면_build_가_거절한다(self):
        ctx = await learning_ctx()
        args = ProposeLearningActivityArgs.model_validate(
            {"items": [item(t) for t in ("a 놀이", "b 놀이", "c 놀이")]}
        )
        result = await propose_learning_activity(ctx, args)
        assert result.error["code"] == "CANDIDATE_REJECTED"

    async def test_식품이_아닌_알레르기는_확인_문구를_남긴다(self):
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="라텍스", status="active")])
        ctx = await learning_ctx(safety=safety)
        await propose_learning_activity(
            ctx, learning_args(ctx, "블록 세기", "색깔 찾기", "모양 맞추기")
        )
        assert ctx.state.cautions and "라텍스" in ctx.state.cautions[0]


def routine_obs(n, subject, level, category="self_care"):
    return RoutineObservation(
        id=UUID(int=700 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=1),
        subject=subject,
        routine_category=category,
        polarity=0,
        assistance_level=level,
    )


async def routine_ctx(months=30, observations=()):
    ctx = context(
        birth_for(months),
        docs=InMemoryGrowthDocs.from_seed(),
        memory=InMemoryGrowthMemory(routine=list(observations)),
    )
    ctx.state.gate = await build_gate(ctx, GrowthTaskType.ROUTINE_COACHING)
    if observations:
        await search_routine_memory(ctx, SearchRoutineMemoryArgs())
    return ctx


def plan(category, subject, items=()):
    return ProposeRoutinePlanArgs.model_validate(
        {"category": category, "subject": subject, "items": list(items)}
    )


class TestRoutine:
    async def test_조회_없이_내면_근거_0_이라_되묻는다(self):
        ctx = await routine_ctx()
        result = await propose_routine_plan(ctx, plan("self_care", "양치"))
        assert result.success
        assert ctx.state.question == "양치은(는) 요즘 어느 정도까지 혼자 하나요?"
        assert ctx.state.suggestions == ()

    async def test_고른_단계를_문장으로_옮기면_개인화_한_개다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        (picked,) = ctx.state.steps.values()
        step_id = str(picked.step.row.id)
        evidence = [{"id": str(UUID(int=701)), "note": "어제 양치를 어른이 다 해 줬어요"}]
        result = await propose_routine_plan(
            ctx,
            plan(
                "self_care",
                "양치",
                [
                    {
                        "step_id": step_id,
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": evidence,
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            ),
        )
        assert result.success
        (draft,) = ctx.state.suggestions
        assert draft.kind == "personalized"
        assert {c.ref.kind for c in draft.citations} == {"observation_routine", "growth_doc"}
        assert ctx.state.capacity == 1

    async def test_고르지_않은_step_id_는_거절한다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        evidence = [{"id": str(UUID(int=701)), "note": "어제 기록"}]
        result = await propose_routine_plan(
            ctx,
            plan(
                "self_care",
                "양치",
                [
                    {
                        "step_id": str(UUID(int=999)),
                        "next_step": "혼자 닦아요",
                        "how": "혼자",
                        "evidence": evidence,
                        "reason": "이유",
                    }
                ],
            ),
        )
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"

    async def test_사슬이_없는_subject_는_doc_no_row_로_닫는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "신발 신기", "partial_assist")])
        await propose_routine_plan(ctx, plan("self_care", "신발 신기"))
        assert ctx.state.closing == "doc.no_row" and ctx.state.suggestions == ()

    async def test_사슬_끝이면_chain_end_로_닫는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "손 씻기", "independent")])
        await propose_routine_plan(ctx, plan("self_care", "손 씻기"))
        assert ctx.state.closing == "next_step.chain_end"

    async def test_36개월_전_습관은_교정_대신_닫는다(self):
        ctx = await routine_ctx(
            months=30, observations=[routine_obs(1, "손톱 물기", None, "habit")]
        )
        await propose_routine_plan(ctx, plan("habit", "손톱 물기"))
        assert ctx.state.closing == "closed.habit_under36"

    async def test_의료_처치_subject_는_닫는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        await propose_routine_plan(ctx, plan("self_care", "안약 넣기"))
        assert ctx.state.closing == "closed.medical_routine"

    async def test_음식_용어가_든_단계_문장은_빠진다(self):
        ctx = await routine_ctx(
            observations=[routine_obs(1, "숟가락 쓰기", "full_assist", "mealtime")]
        )
        (picked,) = ctx.state.steps.values()
        step_id = str(picked.step.row.id)
        evidence = [{"id": str(UUID(int=701)), "note": "어제 기록"}]
        await propose_routine_plan(
            ctx,
            plan(
                "mealtime",
                "숟가락 쓰기",
                [
                    {
                        "step_id": step_id,
                        "next_step": "땅콩버터를 떠먹어 봐요",
                        "how": "숟가락으로",
                        "evidence": evidence,
                        "reason": "한 칸 올려요",
                    }
                ],
            ),
        )
        assert ctx.state.suggestions == () and ctx.state.excluded == ("땅콩버터를 떠먹어 봐요",)

    async def test_루틴_기록_없이_관심만_인용하면_거절한다(self):
        affinity = AffinityRow(
            id=UUID(int=950),
            merge_key="공룡",
            state="confirmed",
            polarity=1,
            strength=0.9,
            last_observed_on=TODAY,
        )
        ctx = context(
            birth_for(30),
            docs=InMemoryGrowthDocs.from_seed(),
            memory=InMemoryGrowthMemory(
                routine=[routine_obs(1, "양치", "full_assist")], affinities=[("activity", affinity)]
            ),
        )
        ctx.state.gate = await build_gate(ctx, GrowthTaskType.ROUTINE_COACHING)
        await search_routine_memory(ctx, SearchRoutineMemoryArgs())
        await search_affinity(ctx, SearchAffinityArgs(domains=["activity"]))
        (picked,) = ctx.state.steps.values()
        result = await propose_routine_plan(
            ctx,
            plan(
                "self_care",
                "양치",
                [
                    {
                        "step_id": str(picked.step.row.id),
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": [{"id": str(affinity.id), "note": "공룡을 좋아해요"}],
                        "reason": "공룡 칫솔로 한 칸만 올려요",
                    }
                ],
            ),
        )
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert ctx.state.suggestions == ()
