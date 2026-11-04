"""출력 tool — 안전 필터 → 금지 표현 → 근거 → build → 개수.

루틴은 닫힘 · 되묻기 · 단계 대조가 먼저다.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.common.evidence import AffinityRow, RankedEvidence
from app.agents.common.refs import Ref, count_child_records
from app.agents.growth.context import build_gate
from app.agents.growth.review import (
    GENERAL_REASON,
    GUIDANCE,
    REQUESTED_REASON,
    RejectReason,
    step_of,
)
from app.agents.growth.schemas.learning import ProposeLearningActivityArgs
from app.agents.growth.schemas.memory import (
    SearchActivityMemoryArgs,
    SearchAffinityArgs,
    SearchEducationMemoryArgs,
    SearchRoutineMemoryArgs,
)
from app.agents.growth.schemas.routine import ProposeRoutinePlanArgs
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import InMemoryGrowthMemory, InMemorySafety
from app.agents.growth.store.ports import (
    ActivityObservation,
    EducationObservation,
    GrowthDocRow,
    RoutineObservation,
    SafetyEntry,
)
from app.agents.growth.tools.memory import (
    has_topic_memory,
    search_activity_memory,
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

    async def test_화면_후보는_화면_낱말이나_화면_근거가_있어야_한다(self):
        ctx = await learning_ctx()
        ctx.state.strict_topic = ("screen",)
        screen_row = GrowthDocRow(
            id=UUID(int=820),
            doc_key="growth.learn.screen.promise",
            row_type="learning_activity",
            title="화면 보는 약속 그림으로 정하기",
            body="가족이 함께 정한 약속을 그림으로 붙여 둔다.",
            min_month=36,
            max_month=72,
            area="physical_health",
            tags=("physical_health", "screen"),
        )
        ctx.state.seen_docs[screen_row.id] = screen_row
        fixture = doc_pick(ctx)  # 화면 행이 아닌 고정 행
        promise = {"id": str(screen_row.id), "note": "화면 약속 예시를 참고했어요"}
        args = ProposeLearningActivityArgs.model_validate(
            {
                "items": [
                    item("신호등 놀이", evidence=[fixture]),
                    item("게임 끝나면 블록 쌓기", evidence=[fixture]),
                    item("그림 그리기", evidence=[promise]),
                ]
            }
        )
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("신호등 놀이",)

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
        assert ctx.state.question == "양치는 요즘 어느 정도까지 혼자 하나요?"
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

    async def test_맞는_단계가_없고_모델이_비워_냈으면_묻지_않고_자료_없음으로_닫는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        await propose_routine_plan(ctx, plan("self_care", "기저귀 떼기"))
        # 사슬이 없는 행동이다. 물어서 수준을 알아도 낼 단계가 없어서 묻지 않고 자료 없음으로 닫는다
        assert ctx.state.closing == "doc.no_row" and ctx.state.question is None

    async def test_답한_행동의_수준을_못_읽으면_되묻지_않고_안내로_닫는다(self):
        """답을 저장했는데 수준도 수행 결과도 비었다. 추정도, 같은 질문의 되풀이도 없다."""
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", None)])
        ctx.state.answered_subject = "양치"
        result = await propose_routine_plan(ctx, plan("self_care", "양치"))
        assert result.success
        assert ctx.state.closing == "next_step.level_unread"
        assert ctx.state.question is None and ctx.state.suggestions == ()

    async def test_답하지_않은_행동은_수준이_없으면_되묻는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "손 씻기", None)])
        ctx.state.answered_subject = "양치"
        await propose_routine_plan(ctx, plan("self_care", "손 씻기"))
        assert ctx.state.question is not None and "손 씻기" in ctx.state.question
        assert ctx.state.closing is None

    async def test_되묻는_길은_사슬을_한_번만_읽는다(self, monkeypatch):
        """사슬 찾기 · 자료 없음 판정 · 저장 이름이 같은 것을 다시 읽으면 20초 안의 왕복이 는다."""
        ctx = await routine_ctx()
        reads: list[int] = []
        search = ctx.ports.docs.search

        async def counted(**kwargs):
            reads.append(1)
            return await search(**kwargs)

        monkeypatch.setattr(ctx.ports.docs, "search", counted)
        await propose_routine_plan(ctx, plan("self_care", "양치"))
        assert ctx.state.question is not None
        assert len(reads) == 1

    async def test_사슬_없는_행동은_묻지_않고_자료_없음으로_닫는다(self):
        """물어서 수준을 알아도 낼 단계가 없다. 답을 받고 "자료가 아직 없어요" 로 끝내지 않는다."""
        ctx = await routine_ctx()
        result = await propose_routine_plan(ctx, plan("mealtime", "밥 먹을 때 돌아다니기"))
        assert result.success
        assert ctx.state.closing == "doc.no_row" and ctx.state.question is None

    async def test_사슬_없는_카테고리는_묻지_않는다(self):
        ctx = await routine_ctx()
        await propose_routine_plan(ctx, plan("transition", "자리 바꾸기"))
        assert ctx.state.closing == "doc.no_row" and ctx.state.question is None

    async def test_단계를_낸_제출은_자료_판정을_건너뛴다(self):
        """ "아침 씻기" 처럼 행동이 묶인 요청은 사슬 이름과 안 맞아도 모델이 낸 단계를 본다."""
        ctx = await routine_ctx()
        item = {
            "step_id": str(UUID(int=999)),
            "next_step": "세수부터 같이 해 봐요",
            "how": "물을 함께 만져요",
            "evidence": [],
            "reason": "아침마다 같이 해요",
        }
        await propose_routine_plan(ctx, plan("self_care", "아침 씻기", [item]))
        assert ctx.state.closing != "doc.no_row"

    async def test_습관은_다른_습관_기록이_있어도_습관_질문이다(self):
        """손톱 기록(trigger 있음)이 있어도 손가락 빨기는 "얼마나 혼자 하나요" 로 묻지 않는다."""
        obs = routine_obs(1, "손톱 물어뜯기", None, "habit", trigger="긴장할 때")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM,))
        await propose_routine_plan(ctx, plan("habit", "손가락 빨기"))
        assert ctx.state.question == "요즘도 손가락 빨기 하나요?"

    async def test_습관은_묻기_전에_자료를_판정하지_않는다(self):
        """습관 전략 행은 trigger 태그로 고른다 — 묻기 전에는 맞는 행이 있는지 모른다."""
        ctx = await routine_ctx(months=40)
        await propose_routine_plan(ctx, plan("habit", "손가락 빨기"))
        assert ctx.state.question == "요즘도 손가락 빨기 하나요?" and ctx.state.closing is None

    async def test_맞는_전략_행이_없는_예절은_묻지_않는다(self):
        ctx = await routine_ctx(months=40)
        await propose_routine_plan(ctx, plan("social_manner", "인사하기"))
        assert ctx.state.closing == "doc.no_row" and ctx.state.question is None

    async def test_맞는_전략_행이_있는_예절은_지금처럼_묻는다(self):
        ctx = await routine_ctx(months=40, rows=(GREETING,))
        await propose_routine_plan(ctx, plan("social_manner", "인사하기"))
        assert ctx.state.question is not None and ctx.state.closing is None

    async def test_습관_되묻기는_요청한_행동_이름으로_묻는다(self):
        """GAP-2 — 손가락 빨기를 물었는데 같은 습관 카테고리의 손톱 관찰 이름으로 묻지 않는다.

        손톱 기록은 손가락 빨기의 근거가 아니라서 기록이 없을 때와 같이 지금도 하는지 묻는다."""
        ctx = await routine_ctx(
            months=48, observations=[routine_obs(1, "손톱 물어뜯기", None, "habit")]
        )
        await propose_routine_plan(ctx, plan("habit", "손가락 빨기"))
        assert ctx.state.question == "요즘도 손가락 빨기 하나요?"


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
        assert ctx.state.question == "손톱 물어뜯기는 주로 어떤 상황에서 하나요?"
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


DINO_LIKE = AffinityRow(
    id=UUID(int=830),
    merge_key="공룡",
    state="confirmed",
    polarity=1,
    strength=0.9,
    last_observed_on=TODAY - timedelta(days=3),
    observed_days=3,
)
READING = EducationObservation(
    id=UUID(int=831),
    child_id=CHILD,
    observed_on=TODAY - timedelta(days=2),
    subject="글 읽기",
    topic="4문장 이상 글 읽기",
    polarity=1,
)
READING_HARD = replace(READING, id=UUID(int=832), polarity=-1)
READING_OLD = AffinityRow(
    id=UUID(int=833),
    merge_key="글 읽기",
    state="confirmed",
    polarity=1,
    strength=0.9,
    last_observed_on=TODAY - timedelta(days=200),
    observed_days=3,
)
GAME_PLAY = ActivityObservation(
    id=UUID(int=834),
    child_id=CHILD,
    observed_on=TODAY - timedelta(days=1),
    subject="게임",
    activity="휴대폰 게임",
    polarity=1,
)


async def topic_ctx(topic, *, education=(), affinities=(), activities=()):
    """주제를 읽어 둔 교육 컨텍스트. 기억 조회까지 해서 근거로 달 수 있는 id 를 만들어 둔다."""
    ctx = await learning_ctx(
        memory=InMemoryGrowthMemory(
            education=list(education),
            affinities=[("education", a) for a in affinities],
            activities=list(activities),
        )
    )
    await search_education_memory(ctx, SearchEducationMemoryArgs())
    if activities:
        await search_activity_memory(ctx, SearchActivityMemoryArgs())
    ctx.state.topic = topic
    return ctx


def basis_args(ctx, *ids, doc=True, reason="이 나이에 많이 하는 활동이에요"):
    picks = [{"id": str(key), "note": "기록을 참고했어요"} for key in ids]
    if doc:
        picks.append(doc_pick(ctx))
    titles = ("글자 찾기", "이야기 이어 말하기", "낱말 카드 놀이")
    return ProposeLearningActivityArgs.model_validate(
        {"items": [item(t, evidence=picks, reason=reason) for t in titles]}
    )


def child_refs(draft):
    return count_child_records(tuple(c.ref for c in draft.citations))


class TestTopicEvidence:
    """요청 주제의 아이 기록이 있을 때만 개인화다 — 관심만으로는 일반 추천이다."""

    async def test_주제_기록이_없으면_아이_기록_근거를_빼고_일반_추천이다(self):
        ctx = await topic_ctx(("communication",), affinities=[DINO_LIKE])
        result = await propose_learning_activity(ctx, basis_args(ctx, DINO_LIKE.id))
        assert result.success and len(ctx.state.suggestions) == 3
        for draft in ctx.state.suggestions:
            assert draft.kind == "general" and draft.reason == GENERAL_REASON
            assert child_refs(draft) == 0

    async def test_주제_기록이_있으면_관심_기록도_같이_남긴다(self):
        ctx = await topic_ctx(("communication",), education=[READING], affinities=[DINO_LIKE])
        result = await propose_learning_activity(ctx, basis_args(ctx, READING.id, DINO_LIKE.id))
        assert result.success
        for draft in ctx.state.suggestions:
            assert draft.kind == "personalized"
            assert {c.ref.id for c in draft.citations} >= {READING.id, DINO_LIKE.id}

    async def test_공룡만_달고_예시가_없으면_주제_근거_안내로_거절한다(self):
        ctx = await topic_ctx(("communication",), affinities=[DINO_LIKE])
        result = await propose_learning_activity(ctx, basis_args(ctx, DINO_LIKE.id, doc=False))
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert GUIDANCE[RejectReason.TOPIC_EVIDENCE] in result.error["message"]

    async def test_주제가_없으면_지금과_같다(self):
        ctx = await topic_ctx((), affinities=[DINO_LIKE])
        result = await propose_learning_activity(ctx, basis_args(ctx, DINO_LIKE.id, doc=False))
        assert result.success
        assert {d.kind for d in ctx.state.suggestions} == {"personalized"}

    async def test_6개월_지난_주제_기록뿐이면_일반_추천이다(self):
        ctx = await topic_ctx(("communication",), affinities=[DINO_LIKE, READING_OLD])
        result = await propose_learning_activity(ctx, basis_args(ctx, READING_OLD.id, DINO_LIKE.id))
        assert result.success
        for draft in ctx.state.suggestions:
            assert draft.kind == "general" and child_refs(draft) == 0

    async def test_어려워한_주제_기록을_도울_대상으로_쓰면_개인화다(self):
        ctx = await topic_ctx(("communication",), education=[READING_HARD])
        result = await propose_learning_activity(
            ctx,
            basis_args(
                ctx, READING_HARD.id, doc=False, reason="글 읽기를 어려워해서 같이 읽어 봐요"
            ),
        )
        assert result.success
        assert {d.kind for d in ctx.state.suggestions} == {"personalized"}

    async def test_게임_기록을_근거로_달면_개인화다(self):
        ctx = await topic_ctx(("screen",), affinities=[DINO_LIKE], activities=[GAME_PLAY])
        result = await propose_learning_activity(
            ctx, basis_args(ctx, GAME_PLAY.id, DINO_LIKE.id, doc=False)
        )
        assert result.success
        assert {d.kind for d in ctx.state.suggestions} == {"personalized"}

    async def test_게임_기록_없이_공룡과_예시만_달면_일반_추천이다(self):
        ctx = await topic_ctx(("screen",), affinities=[DINO_LIKE], activities=[GAME_PLAY])
        result = await propose_learning_activity(ctx, basis_args(ctx, DINO_LIKE.id))
        assert result.success
        assert {d.kind for d in ctx.state.suggestions} == {"general"}

    async def test_주제_기록이_있는지_본다(self):
        with_topic = await topic_ctx(("communication",), education=[READING])
        without = await topic_ctx(("communication",), affinities=[DINO_LIKE])
        stale = await topic_ctx(("communication",), affinities=[READING_OLD])
        game = await topic_ctx(("screen",), activities=[GAME_PLAY])
        assert await has_topic_memory(with_topic, ("communication",)) is True
        assert await has_topic_memory(without, ("communication",)) is False
        assert await has_topic_memory(stale, ("communication",)) is False
        assert await has_topic_memory(game, ("screen",)) is True
        assert await has_topic_memory(game, ("communication",)) is False


def step_item(picked, *, evidence_ids=()):
    return {
        "step_id": str(picked.step.row.id),
        "next_step": "같이 해 봐요",
        "how": "손을 포개요",
        "evidence": [{"id": str(UUID(int=i)), "note": "기록을 봤어요"} for i in evidence_ids],
        "reason": "지금 수준에 맞춰 한 칸만 올려요",
    }


def picked_for(ctx, name):
    return next(p for p in ctx.state.steps.values() if name in p.subject)


class TestRoutineBehaviorEvidence:
    """루틴 근거는 요청한 행동의 기록이다 — 같은 카테고리의 다른 행동 기록은 근거가 아니다."""

    async def test_다른_행동_기록만_있으면_그_행동으로_되묻는다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "손 씻기", "partial_assist")])
        wash = picked_for(ctx, "손 씻기")
        result = await propose_routine_plan(
            ctx, plan("self_care", "양치", [step_item(wash, evidence_ids=(701,))])
        )
        assert result.success and ctx.state.suggestions == ()
        assert ctx.state.question == "양치는 요즘 어느 정도까지 혼자 하나요?"
        assert ctx.state.asked is not None and ctx.state.asked[0] == "ask.routine_current"

    async def test_다른_행동_기록을_근거로_단_단계는_거절한다(self):
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "양치", "full_assist"),
                routine_obs(2, "손 씻기", "partial_assist"),
            ]
        )
        brush = picked_for(ctx, "양치")
        result = await propose_routine_plan(
            ctx, plan("self_care", "양치", [step_item(brush, evidence_ids=(702,))])
        )
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert ctx.state.suggestions == ()

    async def test_요청_행동의_기록을_달면_개인화다(self):
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "양치", "full_assist"),
                routine_obs(2, "손 씻기", "partial_assist"),
            ]
        )
        brush = picked_for(ctx, "양치")
        result = await propose_routine_plan(
            ctx, plan("self_care", "양치", [step_item(brush, evidence_ids=(701,))])
        )
        assert result.success and [d.kind for d in ctx.state.suggestions] == ["personalized"]

    async def test_다른_습관_기록만_있으면_단계를_내도_요즘도_하는지_묻는다(self):
        obs = routine_obs(1, "손톱 물어뜯기", None, "habit", trigger="긴장할 때")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM,))
        calm = next(p for p in ctx.state.steps.values() if p.step.row is not None)
        result = await propose_routine_plan(
            ctx, plan("habit", "손가락 빨기", [step_item(calm, evidence_ids=(701,))])
        )
        assert result.success and ctx.state.suggestions == ()
        assert ctx.state.question == "요즘도 손가락 빨기 하나요?"

    async def test_사슬이_안_맞는_여러_행동_요청은_카테고리_근거_그대로다(self):
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "양치", "full_assist"),
                routine_obs(2, "손 씻기", "partial_assist"),
            ]
        )
        items = [
            step_item(picked_for(ctx, "양치"), evidence_ids=(701,)),
            step_item(picked_for(ctx, "손 씻기"), evidence_ids=(702,)),
        ]
        result = await propose_routine_plan(ctx, plan("self_care", "아침 씻기", items))
        assert result.success and len(ctx.state.suggestions) == 2


class TestGrowthGuard:
    """진단명 · 강압 · 구매 유도가 든 후보는 고치지 않고 빼서 재호출 제외 목록으로 보낸다."""

    async def test_가드에_걸린_교육_후보는_빠지고_제외_목록에_간다(self):
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "손들고 서 있기 놀이", "색깔 찾기", "모양 맞추기")
        )
        assert result.success and len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("손들고 서 있기 놀이",)

    async def test_보이는_이유와_근거_메모에_걸리면_뺀다(self):
        # 이유가 보이는 것은 개인화 추천이다 — 아이 기록을 단 후보의 이유와, 문서 행 근거의 메모
        ctx = await memory_ctx()
        mine = {"id": str(EDU.id), "note": "계단 숫자를 셌어요"}
        args = ProposeLearningActivityArgs.model_validate(
            {
                "items": [
                    item("색깔 찾기", evidence=[mine], reason="ADHD 걱정이 있어서 골랐어요"),
                    item(
                        "모양 맞추기",
                        evidence=[{**doc_pick(ctx), "note": "유료 강의를 참고했어요"}],
                    ),
                    item("블록 세기", evidence=[doc_pick(ctx)]),
                ]
            }
        )
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 1
        assert ctx.state.excluded == ("색깔 찾기", "모양 맞추기")

    async def test_일반_추천은_덮어쓰이는_모델_이유로_빼지_않는다(self):
        # 일반 추천의 이유는 코드 문구로 덮여 화면에 안 나간다 — 안 보이는 글로 후보를 잃지 않는다
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx,
            learning_args(
                ctx,
                "색깔 찾기",
                "모양 맞추기",
                "블록 세기",
                reason="ADHD 걱정이 있는 집에도 좋아요",
            ),
        )
        assert result.success and len(ctx.state.suggestions) == 3 and ctx.state.excluded == ()
        assert {d.reason for d in ctx.state.suggestions} == {GENERAL_REASON}

    async def test_걸린_낱말은_결과에_싣지_않는다(self):
        ctx = await learning_ctx()
        result = await propose_learning_activity(
            ctx, learning_args(ctx, "손들고 서 있기 놀이", "색깔 찾기", "모양 맞추기")
        )
        assert "손들고" not in str(result.to_payload())

    async def test_가드에_걸린_루틴_후보는_뺀다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        (picked,) = ctx.state.steps.values()
        bad = {
            **step_item(picked, evidence_ids=(701,)),
            "how": "말을 안 들으면 손들고 서 있게 해요",
        }
        result = await propose_routine_plan(ctx, plan("self_care", "양치", [bad]))
        assert result.success and ctx.state.suggestions == ()
        assert ctx.state.excluded == ("같이 해 봐요",)

    async def test_가드에_걸리지_않는_루틴_후보는_그대로_나간다(self):
        ctx = await routine_ctx(observations=[routine_obs(1, "양치", "full_assist")])
        (picked,) = ctx.state.steps.values()
        result = await propose_routine_plan(
            ctx, plan("self_care", "양치", [step_item(picked, evidence_ids=(701,))])
        )
        assert result.success and len(ctx.state.suggestions) == 1 and ctx.state.excluded == ()


SOCIAL_RECORD = EducationObservation(
    id=UUID(int=840),
    child_id=CHILD,
    observed_on=TODAY - timedelta(days=1),
    subject="약속 지키기",
    topic="약속 지키기",
    polarity=1,
)


class TestRecordTopicNames:
    """기록이 주제 안인지는 주제 낱말로만 본다 — 후보 글의 활동 낱말(약속 · 규칙)은 안 쓴다."""

    async def test_사회_기록은_화면_주제의_근거가_아니다(self):
        ctx = await topic_ctx(("screen",), education=[SOCIAL_RECORD])
        result = await propose_learning_activity(ctx, basis_args(ctx, SOCIAL_RECORD.id, doc=False))
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert GUIDANCE[RejectReason.TOPIC_EVIDENCE] in result.error["message"]

    async def test_사회_기록만_있으면_주제_기록이_없다(self):
        ctx = await topic_ctx(("screen",), education=[SOCIAL_RECORD])
        assert await has_topic_memory(ctx, ("screen",)) is False


class TestRoutineBehaviorNames:
    async def test_두_행동을_함께_말하면_두_행동_모두_근거다(self):
        ctx = await routine_ctx(
            observations=[
                routine_obs(1, "양치", "full_assist"),
                routine_obs(2, "손 씻기", "partial_assist"),
            ]
        )
        items = [
            step_item(picked_for(ctx, "양치"), evidence_ids=(701,)),
            step_item(picked_for(ctx, "손 씻기"), evidence_ids=(702,)),
        ]
        result = await propose_routine_plan(ctx, plan("self_care", "양치하고 손 씻기", items))
        assert result.success and len(ctx.state.suggestions) == 2
        assert {d.kind for d in ctx.state.suggestions} == {"personalized"}

    async def test_답한_행동이_모델이_적은_이름과_달라도_다시_묻지_않는다(self):
        obs = routine_obs(1, "손가락 빨기", None, "habit", trigger="긴장할 때")
        ctx = await routine_ctx(months=40, observations=[obs], rows=(CALM,))
        ctx.state.answered_subject = "손가락 빨기"
        ctx.state.recorded_subjects = ("손가락 빨기",)
        ctx.state.said_texts = ("손가락 빨기",)
        calm = next(p for p in ctx.state.steps.values() if p.step.row is not None)
        result = await propose_routine_plan(
            ctx, plan("habit", "손가락 빠는 것", [step_item(calm, evidence_ids=(701,))])
        )
        assert result.success and ctx.state.question is None
        assert len(ctx.state.suggestions) == 1


async def requested_ctx(*, said=("영어 어떻게 가르쳐?",), **kwargs):
    """주제는 읽었는데 그 주제의 [예시] 행이 없는 요청 기반 교육 컨텍스트."""
    ctx = await topic_ctx(("other",), **kwargs)
    ctx.state.seen_docs = {}
    ctx.state.requested = True
    ctx.state.said_texts = tuple(said)
    return ctx


def free_args(ctx, *ids, steps=("그림 카드를 보며 이름을 말해요",), reason="요청에 맞춰 골랐어요"):
    """[예시]가 없으니 근거는 아이 기록뿐이다. ids 가 없으면 근거 없이 낸다."""
    picks = [{"id": str(key), "note": "기록을 참고했어요"} for key in ids]
    titles = ("영어 그림 카드 놀이", "알파벳 찾기 놀이", "영어 노래 따라 하기")
    return ProposeLearningActivityArgs.model_validate(
        {"items": [item(t, steps=steps, evidence=picks, reason=reason) for t in titles]}
    )


class TestRequestedGeneral:
    """보호자가 주제를 정해 물었는데 그 주제의 문서 행이 없으면 요청이 기준인 일반 추천이다."""

    async def test_근거_없이_낸_후보는_요청_기반_일반_추천이다(self):
        ctx = await requested_ctx()
        result = await propose_learning_activity(ctx, free_args(ctx))
        assert result.success and len(ctx.state.suggestions) == 3
        for draft in ctx.state.suggestions:
            assert draft.kind == "general" and draft.requested is True
            assert draft.reason == REQUESTED_REASON
            assert draft.citations == ()

    async def test_요청_기반이_아니면_근거_없는_후보는_거절한다(self):
        ctx = await requested_ctx()
        ctx.state.requested = False
        result = await propose_learning_activity(ctx, free_args(ctx))
        assert not result.success and result.error["code"] == "CANDIDATE_REJECTED"
        assert GUIDANCE[RejectReason.BUILD] in result.error["message"]

    async def test_관심만_달면_아이_기록을_빼고_요청_기반_일반_추천이다(self):
        ctx = await requested_ctx(affinities=[DINO_LIKE])
        result = await propose_learning_activity(ctx, free_args(ctx, DINO_LIKE.id))
        assert result.success
        for draft in ctx.state.suggestions:
            assert draft.kind == "general" and draft.requested is True
            assert child_refs(draft) == 0

    async def test_주제_기록을_달면_개인화다(self):
        english = replace(READING, id=UUID(int=840), subject="영어", topic="영어 그림책 보기")
        ctx = await requested_ctx(education=[english])
        result = await propose_learning_activity(
            ctx, free_args(ctx, english.id, reason="영어 그림책을 이어서 골랐어요")
        )
        assert result.success
        for draft in ctx.state.suggestions:
            assert draft.kind == "personalized" and draft.requested is False

    async def test_보호자가_말하지_않은_시간_수치는_뺀다(self):
        ctx = await requested_ctx()
        args = free_args(ctx, steps=("하루 1시간까지 카드를 본다",))
        result = await propose_learning_activity(ctx, args)
        assert result.success
        assert ctx.state.suggestions == ()
        assert len(ctx.state.excluded) == 3

    async def test_보호자가_말한_수치는_그대로_쓴다(self):
        ctx = await requested_ctx(said=("영어 노래를 하루 30분 틀어 주는데 어떻게 가르쳐?",))
        result = await propose_learning_activity(
            ctx, free_args(ctx, steps=("30분 안에 카드를 본다",))
        )
        assert result.success and len(ctx.state.suggestions) == 3

    async def test_아이_월령은_말한_수치로_센다(self):
        ctx = await requested_ctx()
        result = await propose_learning_activity(
            ctx, free_args(ctx, steps=("30개월 아이가 따라 하기 쉬운 카드를 본다",))
        )
        assert result.success and len(ctx.state.suggestions) == 3

    async def test_의료_증상_낱말이_든_후보는_뺀다(self):
        ctx = await requested_ctx()
        result = await propose_learning_activity(
            ctx, free_args(ctx, steps=("눈 깜빡임이 줄도록 카드를 본다",))
        )
        assert result.success and ctx.state.suggestions == ()
        assert len(ctx.state.excluded) == 3

    async def test_요청_기반이_아니면_수치를_보지_않는다(self):
        """문서 행이 받치는 run 에서는 행이 말한 수치를 지우지 않는다."""
        ctx = await topic_ctx(("communication",), affinities=[DINO_LIKE])
        args = basis_args(ctx, DINO_LIKE.id)
        for it in args.items:
            it.steps = ["하루 1시간까지 이야기를 나눈다"]
        result = await propose_learning_activity(ctx, args)
        assert result.success and len(ctx.state.suggestions) == 3
