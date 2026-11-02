"""출력 tool — 안전 필터 → 금지 표현 → 근거 → build → 개수.

루틴은 닫힘 · 되묻기 · 단계 대조가 먼저다.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.common.evidence import AffinityRow, RankedEvidence
from app.agents.common.refs import Ref
from app.agents.growth.context import build_gate
from app.agents.growth.review import step_of
from app.agents.growth.schemas.learning import ProposeLearningActivityArgs
from app.agents.growth.schemas.memory import (
    SearchAffinityArgs,
    SearchEducationMemoryArgs,
    SearchRoutineMemoryArgs,
)
from app.agents.growth.schemas.routine import ProposeRoutinePlanArgs
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemoryGrowthMemory, InMemorySafety
from app.agents.growth.store.ports import EducationObservation, RoutineObservation, SafetyEntry
from app.agents.growth.tools.memory import (
    search_affinity,
    search_education_memory,
    search_routine_memory,
)
from app.agents.growth.tools.propose import propose_learning_activity, propose_routine_plan
from tests.unit.agents.growth.support import (
    CALM,
    CHILD,
    GREETING,
    REST,
    TODAY,
    birth_for,
    context,
    fixture_docs,
)


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
    ctx = context(birth_for(months), docs=fixture_docs(), **kwargs)
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


EDU = EducationObservation(
    id=UUID(int=801),
    child_id=CHILD,
    observed_on=TODAY - timedelta(days=2),
    subject="숫자",
    topic="숫자 세기",
    polarity=1,
)
NUMBERS = AffinityRow(
    id=UUID(int=802),
    merge_key="숫자",
    state="confirmed",
    polarity=1,
    strength=0.9,
    last_observed_on=TODAY - timedelta(days=3),
    observed_days=3,
)


async def memory_ctx():
    """교육 관찰(티어 3) 하나와 확정 관심(티어 1) 하나를 조회해 둔 교육 컨텍스트."""
    ctx = await learning_ctx(
        memory=InMemoryGrowthMemory(education=[EDU], affinities=[("education", NUMBERS)])
    )
    await search_education_memory(ctx, SearchEducationMemoryArgs())
    return ctx


def cited(ctx, first: dict) -> ProposeLearningActivityArgs:
    """첫 후보만 `first` 근거를 달고, 나머지 둘은 [예시] 문서 행을 단다."""
    return ProposeLearningActivityArgs.model_validate(
        {
            "items": [
                item("숫자 세기", evidence=[first], reason="숫자 세기를 이어 가려고 골랐어요"),
                item("색깔 찾기", evidence=[doc_pick(ctx)]),
                item("모양 맞추기", evidence=[doc_pick(ctx)]),
            ]
        }
    )


def spaced(raw: str) -> str:
    """모델이 id 를 옮겨 적다 가운데에 공백을 흘린 모양."""
    return raw[:4] + " " + raw[4:]


class TestLearning:
    async def test_id_안에_공백이_끼어도_알려진_id_와_같으면_받는다(self):
        ctx = await learning_ctx()
        pick = doc_pick(ctx)
        pick["id"] = spaced(pick["id"])
        args = ProposeLearningActivityArgs.model_validate(
            {"items": [item(t, evidence=[pick]) for t in ("블록 세기", "색깔 찾기", "모양 맞추기")]}
        )
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 3

    async def test_공백을_지워도_알려진_id_가_아니면_거절한다(self):
        ctx = await learning_ctx()
        pick = doc_pick(ctx)
        pick["id"] = spaced(pick["id"]).replace("0", "1", 1).replace("a", "b", 1)
        args = ProposeLearningActivityArgs.model_validate(
            {"items": [item(t, evidence=[pick]) for t in ("블록 세기", "색깔 찾기", "모양 맞추기")]}
        )
        result = await propose_learning_activity(ctx, args)
        assert not result.success and result.error["code"] == "EVIDENCE_REQUIRED"

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

    async def test_같은_제목을_두_번_내면_거절한다(self):
        """띄어쓰기 · 문장부호만 다른 것도 같은 활동이다 — 같은 카드가 두 장 나가지 않게."""
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "블록 세기", "블록  세기!", "모양 맞추기")
        )
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert result.error["message"] == (
            "2번 후보: 같은 활동을 두 번 냈다. 후보마다 다른 활동을 낸다."
        )
        assert ctx.state.suggestions == ()

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

    async def test_식품이_아닌_알레르기는_표_낱말이_든_후보를_빼고_문구는_없다(self):
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="라텍스", status="active")])
        ctx = await learning_ctx(safety=safety)
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "고무 도장 찍기", "색깔 찾기", "모양 맞추기")
        )
        assert result.success and len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("고무 도장 찍기",)  # 모델이 낸 제목뿐 — 이름 · 이유는 없다
        assert "라텍스" not in str(result.to_payload())
        assert not hasattr(ctx.state, "cautions")


def routine_obs(n, subject, level, category="self_care", trigger=None):
    return RoutineObservation(
        id=UUID(int=700 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=1),
        subject=subject,
        routine_category=category,
        polarity=0,
        assistance_level=level,
        trigger=trigger,
    )


async def routine_ctx(months=30, observations=(), rows=()):
    ctx = context(
        birth_for(months),
        docs=fixture_docs(*rows),
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


class TestTopic:
    """요청 주제(`state.topic`)와 맞지 않는 교육 후보는 빠지고 재호출 제외 목록으로 간다."""

    async def test_주제와_무관한_후보는_빠지고_제외_목록에_간다(self):
        ctx = await memory_ctx()
        ctx.state.strict_topic = ("communication",)
        mine = [{"id": str(EDU.id), "note": "계단 숫자를 셌어요"}]
        args = ProposeLearningActivityArgs.model_validate(
            {
                "items": [
                    item("공룡 이름 짝맞추기", evidence=mine),
                    item("한글 자음 찾기", evidence=[doc_pick(ctx)]),
                    item("이야기 이어 말하기", evidence=[doc_pick(ctx)]),
                ]
            }
        )
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("공룡 이름 짝맞추기",)

    async def test_주제_영역_예시를_단_후보는_낱말이_없어도_통과한다(self):
        ctx = await learning_ctx()
        ctx.state.strict_topic = ("communication",)
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "블록 세기", "색깔 찾기", "모양 맞추기")
        )
        assert result.success and len(ctx.state.suggestions) == 3
        assert ctx.state.excluded == ()

    async def test_인용한_기억_행이_주제_안이면_낱말이_없어도_통과한다(self):
        ctx = await memory_ctx()
        ctx.state.strict_topic = ("communication",)
        reading = EducationObservation(
            id=UUID(int=811),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=2),
            subject="글 읽기",
            topic="4문장 이상 글 읽기",
            polarity=1,
        )
        ctx.state.seen_evidence[reading.id] = RankedEvidence(
            ref=Ref(kind="observation_education", id=reading.id),
            tier=3,
            label=reading.topic,
            polarity=1,
            observed_on=reading.observed_on,
            subject=reading.subject,
        )
        mine = [{"id": str(reading.id), "note": "긴 글을 읽기 어려워했어요"}]
        args = ProposeLearningActivityArgs.model_validate(
            {"items": [item(t, evidence=mine) for t in ("공룡 놀이", "블록 놀이", "자동차 놀이")]}
        )
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 3
        assert ctx.state.excluded == ()

    async def test_주제_키가_없으면_빼지_않는다(self):
        ctx = await memory_ctx()
        mine = [{"id": str(EDU.id), "note": "계단 숫자를 셌어요"}]
        args = ProposeLearningActivityArgs.model_validate(
            {"items": [item(t, evidence=mine) for t in ("블록 세기", "색깔 찾기", "모양 맞추기")]}
        )
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 3


class TestOverstatedNote:
    """note 의 반복 · 성향 표현은 확정 관심(티어 1) 근거에만 — Activity 와 같은 판정이다."""

    @pytest.mark.parametrize(
        "note", ["늘 숫자를 세요", "숫자 세기를 좋아하는 아이예요", "어제도 숫자를 셌어요"]
    )
    async def test_관찰_근거에_성향_표현을_쓰면_거절한다(self, note):
        ctx = await memory_ctx()
        result = await propose_learning_activity(ctx, cited(ctx, {"id": str(EDU.id), "note": note}))
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert result.error["message"].startswith("1번 후보:")
        assert "tier 1" in result.error["message"]
        assert ctx.state.suggestions == ()

    async def test_확정_관심에는_쓸_수_있다(self):
        ctx = await memory_ctx()
        result = await propose_learning_activity(
            ctx, cited(ctx, {"id": str(NUMBERS.id), "note": "늘 숫자를 세요"})
        )
        assert result.success
        assert ctx.state.suggestions[0].kind == "personalized"

    async def test_문서_행_note_도_본다(self):
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx, cited(ctx, {**doc_pick(ctx), "note": "늘 숫자를 좋아하는 아이에게"})
        )
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"

    @pytest.mark.parametrize("note", ["오늘 숫자를 셌어요", "블록을 늘어놓으며 셌어요"])
    async def test_글자만_겹치는_말은_거절하지_않는다(self, note):
        ctx = await memory_ctx()
        result = await propose_learning_activity(ctx, cited(ctx, {"id": str(EDU.id), "note": note}))
        assert result.success
        assert ctx.state.suggestions[0].kind == "personalized"

    async def test_루틴_관찰에_성향_표현을_쓰면_거절한다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
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
                        "evidence": [{"id": str(UUID(int=701)), "note": "늘 어른이 다 닦아 줘요"}],
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            ),
        )
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert "tier 1" in result.error["message"]


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
            observed_days=3,
        )
        ctx = context(
            birth_for(30),
            docs=fixture_docs(),
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

    async def test_근거로_단_관찰의_행동이_약이면_닫는다(self):
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "양치", "full_assist"),
                routine_obs(2, "약 먹기", "partial_assist"),
            ]
        )
        tooth = next(p for p in ctx.state.steps.values() if p.subject == "양치")
        await propose_routine_plan(
            ctx,
            plan(
                "self_care",
                "양치",
                [
                    {
                        "step_id": str(tooth.step.row.id),
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": [{"id": str(UUID(int=702)), "note": "어제 먹기 싫어했어요"}],
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            ),
        )
        assert ctx.state.closing == "closed.medical_routine" and ctx.state.suggestions == ()

    async def test_step_id_와_근거_id_안의_공백도_같은_id_로_읽는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        (picked,) = ctx.state.steps.values()
        result = await propose_routine_plan(
            ctx,
            plan(
                "self_care",
                "양치",
                [
                    {
                        "step_id": spaced(str(picked.step.row.id)),
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": [
                            {"id": spaced(str(UUID(int=701))), "note": "어제 어른이 다 닦아 줬어요"}
                        ],
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            ),
        )
        assert result.success and len(ctx.state.suggestions) == 1

    async def test_요청한_행동의_단계만_낸다(self):
        """GAP-1 — 양치를 물었는데 같은 카테고리의 손 씻기 단계까지 내라고 하지 않는다."""
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "양치", "full_assist"),
                routine_obs(2, "손 씻기", "full_assist"),
            ]
        )
        tooth = next(p for p in ctx.state.steps.values() if p.subject == "양치")
        result = await propose_routine_plan(
            ctx,
            plan(
                "self_care",
                "양치",
                [
                    {
                        "step_id": str(tooth.step.row.id),
                        "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                        "how": "어른 손을 아이 손 위에 얹어요",
                        "evidence": [
                            {"id": str(UUID(int=701)), "note": "어제 어른이 다 닦아 줬어요"}
                        ],
                        "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                    }
                ],
            ),
        )
        assert result.success and ctx.state.capacity == 1
        (draft,) = ctx.state.suggestions
        assert step_of(draft) == tooth.step.row.id

    async def test_요청한_행동의_사슬이_없으면_다른_행동_단계를_내지_않고_닫는다(self):
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "숟가락 쓰기", "verbal_prompt", "mealtime"),
                routine_obs(2, "앉아서 먹기", "partial_assist", "mealtime"),
            ]
        )
        await propose_routine_plan(ctx, plan("mealtime", "앉아서 먹기"))
        assert ctx.state.closing == "doc.no_row" and ctx.state.suggestions == ()

    async def test_맞는_단계가_없고_모델이_비워_냈으면_그_행동으로_되묻는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        await propose_routine_plan(ctx, plan("self_care", "기저귀 떼기"))
        assert ctx.state.question == "기저귀 떼기은(는) 요즘 어느 정도까지 혼자 하나요?"

    async def test_습관_되묻기는_요청한_행동_이름으로_묻는다(self):
        """GAP-2 — 손가락 빨기를 물었는데 같은 습관 카테고리의 손톱 관찰 이름으로 묻지 않는다."""
        ctx = await routine_ctx(
            months=48, observations=[routine_obs(1, "손톱 물어뜯기", None, "habit")]
        )
        await propose_routine_plan(ctx, plan("habit", "손가락 빨기"))
        assert ctx.state.question == "손가락 빨기은(는) 주로 어떤 상황에서 하나요?"


def strategy_item(row, note: str, reason: str) -> dict:
    return {
        "step_id": str(row.id),
        "next_step": f"{row.title.split(' — ')[0]}를 집에서 해 봐요",
        "how": "어른이 먼저 해 보이고 아이가 따라 해요",
        "evidence": [{"id": str(UUID(int=701)), "note": note}],
        "reason": reason,
    }


class TestStrategyPlan:
    """예절 · 습관은 전략 행이 있으면 열리고, 없으면 지금처럼 doc.no_row 다."""

    async def test_예절_연습_행을_문장으로_옮기면_개인화_추천이다(self):
        ctx = await routine_ctx(
            months=40,
            observations=[routine_obs(1, "인사하기", None, "social_manner")],
            rows=(GREETING,),
        )
        item = strategy_item(GREETING, "어제 어른에게 인사를 안 했어요", "놀이로 먼저 해 봐요")
        result = await propose_routine_plan(ctx, plan("social_manner", "인사하기", [item]))
        assert result.success
        (draft,) = ctx.state.suggestions
        assert draft.kind == "personalized"
        assert ("growth_doc", GREETING.id) in {(c.ref.kind, c.ref.id) for c in draft.citations}
        assert ctx.state.capacity == 1

    async def test_예절_행이_없으면_모델이_지어내지_않게_doc_no_row_로_닫는다(self):
        ctx = await routine_ctx(
            months=40, observations=[routine_obs(1, "인사하기", None, "social_manner")]
        )
        await propose_routine_plan(ctx, plan("social_manner", "인사하기"))
        assert ctx.state.closing == "doc.no_row" and ctx.state.question is None

    async def test_습관은_trigger_와_맞는_대응_행으로_개인화_추천이다(self):
        obs = routine_obs(1, "손톱 물어뜯기", None, "habit", trigger="긴장할 때")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM, REST))
        item = strategy_item(CALM, "긴장할 때 손톱을 물었어요", "긴장한 마음을 말로 먼저 붙여요")
        result = await propose_routine_plan(ctx, plan("habit", "손톱 물어뜯기", [item]))
        assert result.success
        (draft,) = ctx.state.suggestions
        assert draft.kind == "personalized"

    async def test_습관은_trigger_가_없으면_대응_행이_있어도_되묻는다(self):
        obs = routine_obs(1, "손톱 물어뜯기", None, "habit")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM,))
        await propose_routine_plan(ctx, plan("habit", "손톱 물어뜯기"))
        assert ctx.state.question == "손톱 물어뜯기은(는) 주로 어떤 상황에서 하나요?"
        assert ctx.state.suggestions == ()

    async def test_습관_trigger_에_맞는_행이_없으면_doc_no_row_로_닫는다(self):
        obs = routine_obs(1, "손톱 물어뜯기", None, "habit", trigger="졸릴 때")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM,))
        await propose_routine_plan(ctx, plan("habit", "손톱 물어뜯기"))
        assert ctx.state.closing == "doc.no_row"

    async def test_고르지_않은_전략_행은_거절한다(self):
        obs = routine_obs(1, "손톱 물어뜯기", None, "habit", trigger="긴장할 때")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM, REST))
        item = strategy_item(REST, "긴장할 때 손톱을 물었어요", "쉬는 시간을 늘려요")
        result = await propose_routine_plan(ctx, plan("habit", "손톱 물어뜯기", [item]))
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"


async def handwash_ctx(
    *, recorded: tuple[str, ...], request: tuple[str, ...], record: tuple[str, ...] = ()
):
    """손 씻기 기록이 있는 아이. 같은 run 에서 저장한 행동과 요청 · 기록 조각은 인자로 넣는다."""
    ctx = await routine_ctx(observations=[routine_obs(1, "손 씻기", "full_assist")])
    ctx.state.recorded_subjects = recorded
    ctx.state.said_texts = (*request, *record)
    return ctx


def handwash_plan(ctx):
    (picked,) = ctx.state.steps.values()
    item = {
        "step_id": str(picked.step.row.id),
        "next_step": "비누 거품을 같이 내 봐요",
        "how": "어른 손을 아이 손 위에 얹어요",
        "evidence": [{"id": str(UUID(int=701)), "note": "어제 손 씻기를 어른이 다 해 줬어요"}],
        "reason": "지금은 어른이 다 해 주고 있어서 한 칸만 올려요",
    }
    return plan("self_care", "손 씻기", [item])


class TestRecordedBehavior:
    """쪼개진 요청 — 같은 run 에서 보호자가 말한 행동이 요청 행동이다(모델이 적은 행동이 아니다)."""

    async def test_기록한_행동과_다른_행동의_단계는_내지_않는다(self):
        ctx = await handwash_ctx(recorded=("밥 먹기",), request=("어떻게 하면 좋을까?",))
        result = await propose_routine_plan(ctx, handwash_plan(ctx))
        assert result.success
        assert ctx.state.suggestions == ()
        assert "밥 먹기" in ctx.state.question and "손 씻기" not in ctx.state.question

    async def test_요청에_다른_행동이_있으면_모델이_적은_행동을_믿는다(self):
        ctx = await handwash_ctx(recorded=("양치",), request=("손 씻기는 어떻게 가르쳐?",))
        await propose_routine_plan(ctx, handwash_plan(ctx))
        assert len(ctx.state.suggestions) == 1

    @pytest.mark.parametrize("request_text", ["손은 어떻게 씻겨?", "손을 혼자 씻게 하려면?"])
    async def test_조사가_붙은_말로_물어도_모델이_적은_행동을_믿는다(self, request_text):
        ctx = await handwash_ctx(recorded=("양치",), request=(request_text,))
        await propose_routine_plan(ctx, handwash_plan(ctx))
        assert len(ctx.state.suggestions) == 1

    async def test_같이_한_기록_조각에_그_행동이_있어도_모델이_적은_행동을_믿는다(self):
        ctx = await handwash_ctx(
            recorded=("양치",), request=("어떻게 하지?",), record=("손을 안 씻으려고 해",)
        )
        await propose_routine_plan(ctx, handwash_plan(ctx))
        assert len(ctx.state.suggestions) == 1

    async def test_기록한_행동이_없으면_지금처럼_고른다(self):
        ctx = await handwash_ctx(recorded=(), request=("어떻게 하면 좋을까?",))
        await propose_routine_plan(ctx, handwash_plan(ctx))
        assert len(ctx.state.suggestions) == 1


def _miscopied(raw: str) -> str:
    """모델이 id 를 옮겨 적다 한 글자를 잘못 적은 모양."""
    return raw[:-1] + ("0" if raw[-1] != "0" else "1")


class TestUsableIds:
    """모르는 근거 id 를 내면 쓸 수 있는 id 를 알려 준다 — 같은 틀린 id 로 되풀이하지 않게."""

    async def test_교육은_조회_결과와_예시의_id_를_알려_준다(self):
        ctx = await memory_ctx()
        pick = {**doc_pick(ctx), "id": _miscopied(doc_pick(ctx)["id"])}
        result = await propose_learning_activity(ctx, cited(ctx, pick))
        assert not result.success and result.error["code"] == "EVIDENCE_REQUIRED"
        message = result.error["message"]
        assert str(EDU.id) in message
        assert all(str(doc_id) in message for doc_id in ctx.state.seen_docs)

    async def test_틀린_id_는_알려_주는_목록에_없다(self):
        ctx = await learning_ctx()
        wrong = _miscopied(doc_pick(ctx)["id"])
        result = await propose_learning_activity(
            ctx, cited(ctx, {"id": wrong, "note": "이 나이 예시를 참고했어요"})
        )
        assert wrong not in result.error["message"].split("쓸 수 있는 id:")[1]

    async def test_근거_id_가_아닌_이유로_거절하면_id_를_싣지_않는다(self):
        ctx = await memory_ctx()
        result = await propose_learning_activity(
            ctx, cited(ctx, {"id": str(EDU.id), "note": "늘 숫자를 세요"})
        )
        assert not result.success and "tier 1" in result.error["message"]
        assert "쓸 수 있는 id" not in result.error["message"]

    async def test_루틴은_조회한_관찰의_id_만_알려_준다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        (picked,) = ctx.state.steps.values()
        item = {
            "step_id": str(picked.step.row.id),
            "next_step": "칫솔을 같이 쥐고 닦아 봐요",
            "how": "어른 손을 아이 손 위에 얹어요",
            "evidence": [{"id": str(UUID(int=999)), "note": "어제 양치 기록"}],
            "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
        }
        result = await propose_routine_plan(ctx, plan("self_care", "양치", [item]))
        assert result.error["code"] == "EVIDENCE_REQUIRED"
        assert str(UUID(int=701)) in result.error["message"]


class TestRoutineScope:
    """보호자가 말한 루틴 카테고리 밖의 단계는 낼 수 없다 — 행동 이름을 몰라도 막는다."""

    async def test_범위_밖_카테고리로_내면_범위_카테고리로_다시_내라고_한다(self):
        ctx = await handwash_ctx(recorded=(), request=("어떻게 하면 좋을까?",))
        ctx.state.routine_scope = ("mealtime",)
        result = await propose_routine_plan(ctx, handwash_plan(ctx))
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert "mealtime" in result.error["message"]
        assert "search_routine_memory" in result.error["message"]
        assert ctx.state.suggestions == () and ctx.state.question is None

    async def test_범위_안_카테고리는_그대로_낸다(self):
        ctx = await handwash_ctx(recorded=(), request=("손 씻기 어떻게 가르쳐?",))
        ctx.state.routine_scope = ("self_care",)
        await propose_routine_plan(ctx, handwash_plan(ctx))
        assert len(ctx.state.suggestions) == 1

    async def test_범위가_비면_지금처럼_고른다(self):
        ctx = await handwash_ctx(recorded=(), request=("어떻게 하면 좋을까?",))
        ctx.state.routine_scope = ()
        await propose_routine_plan(ctx, handwash_plan(ctx))
        assert len(ctx.state.suggestions) == 1

    async def test_의료_처치는_범위보다_먼저_닫는다(self):
        ctx = await handwash_ctx(recorded=(), request=("약 먹이는 법",))
        ctx.state.routine_scope = ("mealtime",)
        await propose_routine_plan(ctx, plan("self_care", "안약 넣기"))
        assert ctx.state.closing == "closed.medical_routine"
