"""Growth run() — 닫힘(모델 0) · 리듬 안내(모델 0) · tool 루프 · 재호출 1회 · 결과 봉투.

모델은 정해 둔 응답을 순서대로 내는 가짜다 (Activity test_activity_agent.py 와 같은 모양).
"""

import asyncio
import json
import logging
import re
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from app.agents.common.evidence import AffinityRow
from app.agents.common.llm_client import LLMResponse
from app.agents.common.refs import Ref
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.agent import run
from app.agents.growth.readouts import READOUTS, RHYTHM_NO_ROW
from app.agents.growth.review import BOOK_GENERAL_REASON
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.inmemory import (
    InMemoryBooks,
    InMemoryGrowthDocs,
    InMemoryGrowthMemory,
    InMemoryIssuedBooks,
    InMemorySafety,
)
from app.agents.growth.store.ports import (
    ActivityObservation,
    BookRow,
    EducationObservation,
    GrowthDocRow,
    RoutineObservation,
    SafetyEntry,
    UpstreamUnavailable,
)
from app.agents.growth.tools.docs import match_chain, routine_chains, search_growth_doc
from app.agents.growth.tools.routine import pick_next_step
from app.rules.book_evidence import book_evidence_id
from tests.unit.agents.growth.support import (
    CALM,
    CHILD,
    REST,
    TODAY,
    birth_for,
    context,
    fixture_docs,
    task,
)


class FakeLLM:
    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.seen.append([dict(m) for m in messages])
        if self._queue:
            return self._queue.pop(0)
        return LLMResponse(
            message=SimpleNamespace(content="끝", tool_calls=None), usage={}, latency_ms=1
        )


_ids = iter(range(1, 10_000))


def call(name: str, args: dict) -> LLMResponse:
    tool_call = SimpleNamespace(
        id=f"c{next(_ids)}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=[tool_call]), usage={}, latency_ms=1
    )


def learning(*titles: str, doc_id: str) -> LLMResponse:
    items = [
        {
            "title": t,
            "steps": ["하나씩 해 본다"],
            "materials": [],
            "evidence": [{"id": doc_id, "note": "이 나이 예시를 참고했어요"}],
            "reason": "이 나이에 많이 하는 활동이에요",
        }
        for t in titles
    ]
    return call("propose_learning_activity", {"items": items})


def docs_ctx(months=30, **kwargs):
    return context(birth_for(months), docs=fixture_docs(), **kwargs)


async def example_id() -> str:
    """같은 아이 · 같은 날은 같은 [예시]를 받는다 — 빈 모델로 돌려 프롬프트에서 id 를 읽는다."""
    probe = FakeLLM()
    await run(task("learning_suggestion"), docs_ctx(), client=probe)
    system = probe.seen[0][0]["content"]
    return system.split("- id ", 1)[1].split(" ", 1)[0]


class TestLearning:
    async def test_한_번에_세_개를_내면_model_calls_1_이다(self):
        doc_id = await example_id()
        llm = FakeLLM(learning("블록 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id))
        result = await run(
            task("learning_suggestion", "숫자 놀이 추천해줘"), docs_ctx(), client=llm
        )
        assert result.status == "completed" and result.agent == "growth"
        assert result.model_calls == 1 and len(result.suggestions) == 3
        assert result.readouts == ()

    async def test_안전_필터로_빠지면_재호출_1회_뒤_빈자리만_채운다(self):
        doc_id = await example_id()
        llm = FakeLLM(
            learning("땅콩 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id),
            learning("색깔 찾기", "소리 듣기", "그림자 놀이", doc_id=doc_id),
        )
        result = await run(task("learning_suggestion"), docs_ctx(), client=llm)
        assert result.model_calls == 2
        titles = [d.content.split("\n")[0] for d in result.suggestions]
        assert titles == ["색깔 찾기", "모양 맞추기", "소리 듣기"]
        recall = llm.seen[1][-1]["content"]
        assert "땅콩 세기" in recall and "땅콩" not in llm.seen[1][0]["content"]

    async def test_같은_제목을_내면_거절되고_고쳐_낸_것이_나간다(self):
        doc_id = await example_id()
        llm = FakeLLM(
            learning("블록 세기", "블록 세기", "블록 세기", doc_id=doc_id),
            learning("블록 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id),
        )
        result = await run(task("learning_suggestion"), docs_ctx(), client=llm)
        assert result.status == "completed" and result.model_calls == 1
        titles = [d.content.split("\n")[0] for d in result.suggestions]
        assert titles == ["블록 세기", "색깔 찾기", "모양 맞추기"]

    async def test_말로_끝내면_failed_다(self):
        result = await run(task("learning_suggestion"), docs_ctx(), client=FakeLLM())
        assert result.status == "failed" and result.suggestions == ()

    async def test_모델_입력에_등록한_알레르기_이름이_없다(self):
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="라텍스", status="active")])
        llm = FakeLLM()
        await run(task("learning_suggestion"), docs_ctx(safety=safety), client=llm)
        assert "라텍스" not in json.dumps(llm.seen, ensure_ascii=False)

    async def test_꽃가루_아이의_예시에는_표_낱말이_든_행이_없다(self):
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="꽃가루", status="active")])
        probe = FakeLLM()
        await run(task("learning_suggestion"), docs_ctx(months=13, safety=safety), client=probe)
        system = probe.seen[0][0]["content"]
        assert system.count("- id ") == 3
        for title in ("걷다가 멈춰서 구경하기", "나뭇잎 만져 보기", "꽃과 풀 가까이서 보기"):
            assert title not in system
        assert "꽃가루" not in system

    async def test_확인_문구_readout_을_내지_않는다(self):
        doc_id = await example_id()
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="라텍스", status="active")])
        llm = FakeLLM(learning("블록 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id))
        result = await run(task("learning_suggestion"), docs_ctx(safety=safety), client=llm)
        assert result.status == "completed" and result.readouts == ()

    async def test_콩이_든_후보는_빠지고_재호출로_채운다(self):
        """C48-food — 모델 후보 하나에 음식 재료.

        재호출 1회 뒤 음식 용어 없는 3개 (test_expected T04).
        """
        doc_id = await example_id()
        llm = FakeLLM(
            learning("콩으로 숫자 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id),
            learning("소리 듣기", "그림자 놀이", "블록 세기", doc_id=doc_id),
        )
        result = await run(
            task("learning_suggestion", "숫자 놀이 뭐 없어?"), docs_ctx(), client=llm
        )
        assert result.model_calls == 2
        titles = [d.content.split("\n")[0] for d in result.suggestions]
        assert titles == ["색깔 찾기", "모양 맞추기", "소리 듣기"]


def empty_docs_ctx(**kwargs):
    """[예시]가 빈 월령 — 문서 검수에서 draft 로 돌아간 행이 생기면 실제로 온다."""
    return context(birth_for(30), docs=InMemoryGrowthDocs(), **kwargs)


class TestLearningWithoutExamples:
    async def test_예시도_기록도_없으면_모델_없이_doc_no_row_로_닫는다(self):
        llm = FakeLLM()
        result = await run(task("learning_suggestion"), empty_docs_ctx(), client=llm)
        assert (result.status, result.model_calls) == ("blocked", 0)
        assert result.readouts == (READOUTS.render("doc.no_row"),)
        assert llm.seen == []
        # 추천 없이 닫히는 자리라 "알려드려요" 라고 하지 않는다 (#452 리뷰)
        assert result.readouts[0].body == "이 시기에 맞는 자료가 아직 없어서 준비하지 못했어요."

    async def test_시드에_교육_행이_없는_72개월_이상도_모델_없이_닫는다(self):
        ctx = context(birth_for(80), docs=InMemoryGrowthDocs.from_seed())
        # 시드의 교육 행은 72개월 미만까지다. 행이 생기면 이 테스트의 월령을 옮긴다
        assert not await search_growth_doc(
            ctx.ports.docs, task_type=GrowthTaskType.LEARNING_SUGGESTION, months=80
        )
        llm = FakeLLM()
        result = await run(task("learning_suggestion"), ctx, client=llm)
        assert result.readouts == (READOUTS.render("doc.no_row"),) and llm.seen == []

    async def test_6개월_지난_관심만_있어도_닫는다(self):
        """오래된 관심은 build() 가 근거에서 빼서 개인화도 일반 추천도 못 된다 (루트 §2)."""
        old = AffinityRow(
            id=UUID(int=901),
            merge_key="숫자",
            state="confirmed",
            polarity=1,
            strength=0.9,
            last_observed_on=TODAY - timedelta(days=200),
            observed_days=3,
        )
        ctx = empty_docs_ctx(memory=InMemoryGrowthMemory(affinities=[("education", old)]))
        llm = FakeLLM()
        result = await run(task("learning_suggestion"), ctx, client=llm)
        assert result.readouts == (READOUTS.render("doc.no_row"),) and llm.seen == []


def _nature_rows(count: int) -> list[GrowthDocRow]:
    return [
        GrowthDocRow(
            id=uuid5(NAMESPACE_URL, f"topic.nature.{n}"),
            doc_key=f"growth.learn.topic.nature{n}",
            row_type="learning_activity",
            title=f"숫자 블록 세기 {n}",
            body="블록을 하나씩 세어 본다.",
            min_month=24,
            max_month=36,
            area="nature",
        )
        for n in range(count)
    ]


class TestExamplesByTopic:
    async def test_국어_요청은_의사소통_예시만_받는다(self):
        """고정 행은 의사소통 5행 · 자연탐구 20행 — 주제 없이 고르면 자연탐구가 섞인다."""
        ctx = context(birth_for(30), docs=fixture_docs(*_nature_rows(20)))
        await run(
            _task(
                "learning_suggestion",
                "어떻게 도와줘야 좋을까?",
                records=("아이가 국어 공부를 어려워하는데",),
            ),
            ctx,
            client=FakeLLM(),
        )
        assert len(ctx.state.seen_docs) == 3
        assert {row.area for row in ctx.state.seen_docs.values()} == {"communication"}

    async def test_기록_조각의_다른_주제는_요청_주제의_예시를_바꾸지_않는다(self):
        ctx = context(birth_for(30), docs=fixture_docs(*_nature_rows(20)))
        await run(
            _task(
                "learning_suggestion",
                "한글 놀이 추천해줘",
                records=("오늘 어린이집에서 숫자 블록 배웠대",),
            ),
            ctx,
            client=FakeLLM(),
        )
        assert {row.area for row in ctx.state.seen_docs.values()} == {"communication"}

    async def test_기록_조각에만_영어가_있으면_모델_전에_닫지_않는다(self):
        ctx = docs_ctx()
        llm = FakeLLM()
        result = await run(
            _task(
                "learning_suggestion", "집에서 뭐 하면 좋을까?", records=("영어 유치원 다니는데",)
            ),
            ctx,
            client=llm,
        )
        assert result.model_calls == 1 and llm.seen
        assert ctx.state.topic == ()

    async def test_영어만_물었고_기록도_없으면_모델_전에_닫는다(self):
        ctx = docs_ctx()
        llm = FakeLLM()
        result = await run(task("learning_suggestion", "영어 공부 어떻게 시켜?"), ctx, client=llm)
        assert (result.status, result.model_calls) == ("blocked", 0)
        assert result.readouts == (READOUTS.render("doc.no_row"),) and llm.seen == []

    @pytest.mark.parametrize(
        "memory",
        [
            InMemoryGrowthMemory(
                education=[
                    EducationObservation(
                        id=UUID(int=902),
                        child_id=CHILD,
                        observed_on=TODAY - timedelta(days=1),
                        subject="숫자",
                        topic="숫자 세기",
                        polarity=1,
                    )
                ]
            ),
            InMemoryGrowthMemory(
                activities=[
                    ActivityObservation(
                        id=UUID(int=903),
                        child_id=CHILD,
                        observed_on=TODAY - timedelta(days=1),
                        subject="블록",
                        activity="블록 쌓기",
                        polarity=1,
                    )
                ]
            ),
        ],
        ids=["교육_기록", "놀이_기록"],
    )
    async def test_달_수_있는_기록이_있으면_모델까지_간다(self, memory):
        llm = FakeLLM()
        result = await run(task("learning_suggestion"), empty_docs_ctx(memory=memory), client=llm)
        assert llm.seen  # 말로 끝내는 가짜라 failed 지만 모델을 불렀다
        assert result.status == "failed"


class TestRoutine:
    async def test_0_11개월은_모델_없이_리듬_안내_하나다(self):
        result = await run(
            task("routine_coaching", "밤잠 재우기"), docs_ctx(months=8), client=FakeLLM()
        )
        assert result.model_calls == 0 and result.suggestions == ()
        (readout,) = result.readouts
        assert readout.authored_by == "code"

    async def test_근거가_없으면_되묻기_한_줄로_끝난다(self):
        llm = FakeLLM(
            call("propose_routine_plan", {"category": "self_care", "subject": "양치", "items": []})
        )
        result = await run(task("routine_coaching", "양치 혼자 하게"), docs_ctx(), client=llm)
        assert result.status == "completed" and result.model_calls == 1
        assert result.needs_observation == ("양치은(는) 요즘 어느 정도까지 혼자 하나요?",)
        # #452 리뷰 — 질문 자리를 못 받아도 카드가 혼자 서는 안내 한 줄. 0개 안내라 05 묶음
        # 안내(`empty_notice`)도 이 문장이다
        (notice,) = result.readouts
        assert (notice.code, notice.authored_by) == ("empty", "code")
        # 습관(상황 · 지금도 하는지)을 물을 때도 붙어서 "다음 단계" 라고 하지 않는다
        assert notice.body == "지금 어떤지 알아야 맞는 방법을 고를 수 있어요."

    async def test_고른_단계로_개인화_추천_하나와_개수_안내다(self):
        obs = RoutineObservation(
            id=UUID(int=701),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=1),
            subject="양치",
            routine_category="self_care",
            polarity=0,
            assistance_level="full_assist",
        )
        ctx = docs_ctx(memory=InMemoryGrowthMemory(routine=[obs]))
        chains = await routine_chains(ctx.ports.docs, months=30, category="self_care")
        step = pick_next_step(match_chain(chains, "양치"), assistance_level="full_assist")
        llm = FakeLLM(
            call("search_routine_memory", {}),
            call(
                "propose_routine_plan",
                {
                    "category": "self_care",
                    "subject": "양치",
                    "items": [
                        {
                            "step_id": str(step.row.id),
                            "next_step": "칫솔을 같이 쥐고 닦아 봐요",
                            "how": "어른 손을 아이 손 위에 얹어요",
                            "evidence": [{"id": str(obs.id), "note": "어제 어른이 다 닦아 줬어요"}],
                            "reason": "지금은 어른이 다 닦아 주고 있어서 한 칸만 올려요",
                        }
                    ],
                },
            ),
        )
        result = await run(task("routine_coaching", "양치 혼자 하게"), ctx, client=llm)
        assert result.status == "completed" and len(result.suggestions) == 1
        assert result.suggestions[0].kind == "personalized"
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_사슬_끝이면_추천_없이_코드_문구다(self):
        obs = RoutineObservation(
            id=UUID(int=702),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=1),
            subject="손 씻기",
            routine_category="self_care",
            polarity=0,
            assistance_level="independent",
        )
        llm = FakeLLM(
            call("search_routine_memory", {}),
            call(
                "propose_routine_plan",
                {"category": "self_care", "subject": "손 씻기", "items": []},
            ),
        )
        ctx = docs_ctx(memory=InMemoryGrowthMemory(routine=[obs]))
        result = await run(task("routine_coaching", "손 씻기"), ctx, client=llm)
        assert result.status == "blocked" and result.model_calls == 1
        (readout,) = result.readouts
        assert "이미 혼자 잘 하고 있어요" in readout.body

    async def test_재호출은_빠진_단계만_다시_내도_받는다(self):
        observations = [
            RoutineObservation(
                id=UUID(int=710 + n),
                child_id=CHILD,
                observed_on=TODAY - timedelta(days=1),
                subject=subject,
                routine_category="self_care",
                polarity=0,
                assistance_level="full_assist",
            )
            for n, subject in enumerate(("양치", "손 씻기"))
        ]
        ctx = docs_ctx(memory=InMemoryGrowthMemory(routine=observations))
        chains = await routine_chains(ctx.ports.docs, months=30, category="self_care")
        steps = {
            subject: pick_next_step(match_chain(chains, subject), assistance_level="full_assist")
            for subject in ("양치", "손 씻기")
        }

        def item(subject: str, n: int, how: str) -> dict:
            return {
                "step_id": str(steps[subject].row.id),
                "next_step": f"{subject} 한 칸 올려요",
                "how": how,
                "evidence": [{"id": str(observations[n].id), "note": "어제 어른이 다 해 줬어요"}],
                "reason": "지금은 어른이 다 해 주고 있어서 한 칸만 올려요",
            }

        def propose(*items: dict) -> LLMResponse:
            return call(
                "propose_routine_plan",
                {"category": "self_care", "subject": "아침 씻기", "items": list(items)},
            )

        llm = FakeLLM(
            call("search_routine_memory", {}),
            propose(item("양치", 0, "어른 손을 포개요"), item("손 씻기", 1, "땅콩 크림을 발라요")),
            propose(item("손 씻기", 1, "비누 거품을 같이 내요")),
        )
        # 여러 행동을 한 번에 묻는 요청 —
        # 요청 행동과 맞는 단계가 없고 모델이 단계를 냈으니 전부 낸다
        result = await run(task("routine_coaching", "아침에 씻는 걸 혼자 하게"), ctx, client=llm)
        assert result.model_calls == 2 and len(result.suggestions) == 2
        assert [r.code for r in result.readouts] == ["fewer"]  # 단계가 둘뿐이라 개수 안내가 붙는다

    async def test_습관은_trigger_와_맞는_대응_행으로_개인화_추천_하나다(self):
        obs = RoutineObservation(
            id=UUID(int=720),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=1),
            subject="손톱 물어뜯기",
            routine_category="habit",
            polarity=0,
            trigger="긴장할 때",
        )
        ctx = context(
            birth_for(40),
            docs=fixture_docs(CALM, REST),
            memory=InMemoryGrowthMemory(routine=[obs]),
        )
        llm = FakeLLM(
            call("search_routine_memory", {}),
            call(
                "propose_routine_plan",
                {
                    "category": "habit",
                    "subject": "손톱 물어뜯기",
                    "items": [
                        {
                            "step_id": str(CALM.id),
                            "next_step": "긴장한 마음을 말로 먼저 붙여 줘요",
                            "how": "손을 잡고 지금 마음을 한 낱말로 같이 말해요",
                            "evidence": [{"id": str(obs.id), "note": "긴장할 때 물었어요"}],
                            "reason": "긴장할 때 나오는 행동이라 마음을 먼저 다뤄요",
                        }
                    ],
                },
            ),
        )
        result = await run(task("routine_coaching", "긴장하면 손톱을 물어뜯어"), ctx, client=llm)
        assert result.status == "completed" and result.model_calls == 1
        assert [d.kind for d in result.suggestions] == ["personalized"]
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_같은_입력의_기록_조각이_약이면_모델_없이_닫는다(self):
        llm = FakeLLM()
        growth_task = DomainTask(
            run_id="run-1",
            agent="growth",
            task_type="routine_coaching",
            request_texts=("어떻게 하지?",),
            record_texts=("약 먹기 싫어해",),
        )
        result = await run(growth_task, docs_ctx(), client=llm)
        assert (result.status, result.model_calls, llm.seen) == ("blocked", 0, [])
        assert result.readouts == (READOUTS.render("closed.medical_routine"),)


def book(n: int, form: str = "picture") -> BookRow:
    return BookRow(isbn=f"978890000{n:04d}", title=f"책{n}", form=form, author="가")


def pick(
    n: int, *, evidence: list | None = None, reason: str = "공룡이 나오는 그림책이에요"
) -> dict:
    return {"isbn": f"978890000{n:04d}", "evidence": evidence or [], "reason": reason}


def books_ctx(months: int = 30, rows=(), *, issued=(), fail: bool = False, **kwargs):
    return context(
        birth_for(months),
        books=InMemoryBooks(list(rows), fail=fail),
        issued_books=InMemoryIssuedBooks(issued),
        **kwargs,
    )


class FailsSecondSearch(InMemoryBooks):
    """첫 검색은 답하고 두 번째부터 실패한다 — 재호출에서 다시 찾다가 장애가 난 경우."""

    def __init__(self, rows) -> None:
        super().__init__(rows)
        self.searches = 0

    async def search(self, *, keywords, limit):
        self.searches += 1
        if self.searches > 1:
            raise UpstreamUnavailable("도서 조회 실패 (테스트 주입)")
        return await super().search(keywords=keywords, limit=limit)


def rhythm_row(key: str, lo: int, hi: int) -> GrowthDocRow:
    return GrowthDocRow(
        id=uuid5(NAMESPACE_URL, key),
        doc_key=key,
        row_type="rhythm_info",
        title=f"{key} — 제목",
        body=f"{key} 본문",
        min_month=lo,
        max_month=hi,
    )


class TestRhythm:
    """0–11개월 루틴은 모델 없이 리듬 안내 행 하나다 (Growth_Tool_명세 §2 "0–11개월")."""

    async def test_리듬_행이_있으면_모델_없이_그_행이_출처와_함께_나간다(self):
        row = rhythm_row("growth.rhythm.m6_9.order", 6, 9)
        ctx = context(birth_for(8), docs=fixture_docs(row))
        llm = FakeLLM()
        result = await run(task("routine_coaching", "밤잠 재우기"), ctx, client=llm)
        assert (result.status, result.model_calls, result.suggestions) == ("completed", 0, ())
        (readout,) = result.readouts
        assert (readout.kind, readout.title, readout.body) == ("rhythm_info", row.title, row.body)
        assert readout.source_refs == (Ref(kind="growth_doc", id=row.id),)
        assert llm.seen == []

    async def test_월령_밖_리듬_행은_읽지_않고_준비_못_한_안내다(self):
        ctx = context(birth_for(8), docs=fixture_docs(rhythm_row("growth.rhythm.m0_3.order", 0, 3)))
        result = await run(task("routine_coaching"), ctx, client=FakeLLM())
        assert result.readouts == (READOUTS.render(RHYTHM_NO_ROW),)

    async def test_한_구간에_행이_여럿이면_아이와_날짜로_돌려_고른다(self):
        rows = [rhythm_row(f"growth.rhythm.m6_9.r{n}", 6, 9) for n in range(6)]
        docs = fixture_docs(*rows)
        expected = await search_growth_doc(
            docs,
            task_type=GrowthTaskType.ROUTINE_COACHING,
            months=8,
            rotation=f"{CHILD}|{TODAY.isoformat()}",
        )
        assert expected[0].row != rows[0]  # 포트 순서 그대로면 이 테스트가 돌려 고르기를 못 본다
        result = await run(
            task("routine_coaching"), context(birth_for(8), docs=docs), client=FakeLLM()
        )
        assert result.readouts[0].body == expected[0].row.body


class TestBook:
    async def test_도서_포트가_없으면_닫힌다(self):
        result = await run(task("book_suggestion", "공룡 책"), docs_ctx(), client=FakeLLM())
        assert result.status == "blocked" and result.model_calls == 0

    async def test_검색한_책_3권을_일반_추천으로_낸다(self):
        llm = FakeLLM(
            call("search_books", {"keywords": ["공룡"]}),
            call("propose_books", {"items": [pick(1), pick(2), pick(3)]}),
        )
        rows = [book(n) for n in range(1, 6)]
        result = await run(
            task("book_suggestion", "공룡 책 추천해줘"), books_ctx(rows=rows), client=llm
        )

        assert result.status == "completed" and result.model_calls == 1
        assert [d.content for d in result.suggestions] == ["책1(가)", "책2(가)", "책3(가)"]
        assert {d.kind for d in result.suggestions} == {"general"}
        assert all(d.reason == BOOK_GENERAL_REASON for d in result.suggestions)
        assert [(c.ref.kind, c.ref.id) for c in result.suggestions[0].citations] == [
            ("book_catalog", book_evidence_id(book(1).isbn))
        ]
        assert result.readouts == ()

    async def test_배움_기록을_인용하면_개인화고_근거는_그_기록뿐이다(self):
        edu = EducationObservation(
            id=UUID(int=901),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=2),
            subject="공룡",
            topic="공룡 그림을 오래 봤다",
            polarity=1,
        )
        cite = [{"id": str(edu.id), "note": "공룡 그림을 오래 봤어요"}]
        llm = FakeLLM(
            call("search_book_interests", {}),
            call("search_books", {"keywords": ["공룡"]}),
            call("propose_books", {"items": [pick(n, evidence=cite) for n in (1, 2, 3)]}),
        )
        ctx = books_ctx(
            rows=[book(n) for n in range(1, 4)], memory=InMemoryGrowthMemory(education=[edu])
        )
        result = await run(task("book_suggestion", "공룡 책"), ctx, client=llm)

        assert {d.kind for d in result.suggestions} == {"personalized"}
        for draft in result.suggestions:
            # 책 근거는 일반 추천에만 붙는다 — 개인화 도서의 book_catalog 근거는 0행
            # (Food #442 와 같다)
            assert [(c.ref.kind, c.ref.id) for c in draft.citations] == [
                ("observation_education", edu.id)
            ]

    async def test_힘들어하는_생활_행동도_주제가_되고_reason_에_그_행동을_쓰면_개인화다(self):
        brush = RoutineObservation(
            id=UUID(int=902),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=1),
            subject="양치하기",
            routine_category="self_care",
            polarity=-1,
            assistance_level="full_assist",
        )
        cite = [{"id": str(brush.id), "note": "양치를 힘들어한 기록이 있어요"}]
        reason = "양치하기를 힘들어해서 양치 이야기를 담은 그림책이에요"
        llm = FakeLLM(
            call("search_book_interests", {}),
            call("search_books", {"keywords": ["양치"]}),
            call(
                "propose_books",
                {"items": [pick(n, evidence=cite, reason=reason) for n in (1, 2, 3)]},
            ),
        )
        ctx = books_ctx(
            rows=[book(n) for n in range(1, 4)], memory=InMemoryGrowthMemory(routine=[brush])
        )
        result = await run(task("book_suggestion", "책 추천해줘"), ctx, client=llm)

        assert result.status == "completed"
        assert {d.kind for d in result.suggestions} == {"personalized"}
        assert [c.ref.kind for c in result.suggestions[0].citations] == ["observation_routine"]

    async def test_힘들어하는_행동을_reason_에_안_쓰면_그_후보는_거절된다(self):
        brush = RoutineObservation(
            id=UUID(int=902),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=1),
            subject="양치하기",
            routine_category="self_care",
            polarity=-1,
            assistance_level="full_assist",
        )
        cite = [{"id": str(brush.id), "note": "양치를 힘들어한 기록이 있어요"}]
        llm = FakeLLM(
            call("search_book_interests", {}),
            call("search_books", {"keywords": ["양치"]}),
            call("propose_books", {"items": [pick(n, evidence=cite) for n in (1, 2, 3)]}),
        )
        ctx = books_ctx(
            rows=[book(n) for n in range(1, 4)], memory=InMemoryGrowthMemory(routine=[brush])
        )
        result = await run(task("book_suggestion", "책 추천해줘"), ctx, client=llm)

        assert result.status == "failed"  # 거절만 받고 끝났다 — 개인화인 척 나가지 않는다

    async def test_교육_놀이_관심이_함께_모여_점수_순_주제가_모델에게_간다(self):
        edu = EducationObservation(
            id=UUID(int=903),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=2),
            subject="공룡",
            topic="공룡 이름 말하기",
            polarity=1,
        )
        play = ActivityObservation(
            id=UUID(int=904),
            child_id=CHILD,
            observed_on=TODAY - timedelta(days=1),
            subject="공룡 인형",
            activity="공룡 인형 놀이",
            polarity=1,
        )
        llm = FakeLLM(call("search_book_interests", {}))
        ctx = books_ctx(
            rows=[book(1)], memory=InMemoryGrowthMemory(education=[edu], activities=[play])
        )
        await run(task("book_suggestion", "책 추천해줘"), ctx, client=llm)

        tool_result = next(m for m in llm.seen[-1] if m.get("role") == "tool")
        assert "공룡" in tool_result["content"] and "놀이 기록" in tool_result["content"]
        assert "교육 기록" in tool_result["content"]

    async def test_검색이_실패하면_closed_book_api_로_닫는다(self):
        llm = FakeLLM(call("search_books", {"keywords": ["공룡"]}))
        result = await run(
            task("book_suggestion"), books_ctx(rows=[book(1)], fail=True), client=llm
        )

        assert result.status == "blocked" and result.model_calls == 1
        assert result.suggestions == ()
        assert [r.body for r in result.readouts] == [READOUTS.render("closed.book_api").body]

    async def test_8개월은_보드북만_나가고_모자라면_개수_안내가_붙는다(self):
        rows = [book(1), book(2, "board"), book(3)]
        llm = FakeLLM(
            call("search_books", {"keywords": ["동물"]}),
            call("propose_books", {"items": [pick(2)]}),
        )
        result = await run(task("book_suggestion"), books_ctx(8, rows), client=llm)

        assert [d.content for d in result.suggestions] == ["책2(가)"]
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_맞는_책이_없으면_빈_제출로_0개_안내다(self):
        llm = FakeLLM(
            call("search_books", {"keywords": ["동물"]}),
            call("propose_books", {"items": []}),
        )
        result = await run(task("book_suggestion"), books_ctx(8, [book(1)]), client=llm)

        assert result.status == "completed" and result.suggestions == ()
        assert [r.code for r in result.readouts] == ["empty"]

    async def test_금지_표현으로_빠지면_재호출에서_다른_책으로_빈자리만_채운다(self):
        llm = FakeLLM(
            call("search_books", {"keywords": ["공룡"]}),
            call(
                "propose_books",
                {"items": [pick(1, reason="숫자에 재능이 있어서 골랐어요"), pick(2), pick(3)]},
            ),
            call("propose_books", {"items": [pick(2), pick(4), pick(5)]}),  # 처음 고른 책2 → 거절
            call("propose_books", {"items": [pick(4), pick(5)]}),
        )
        result = await run(
            task("book_suggestion"), books_ctx(rows=[book(n) for n in range(1, 6)]), client=llm
        )

        assert result.model_calls == 2
        assert [d.content for d in result.suggestions] == ["책2(가)", "책3(가)", "책4(가)"]
        recall = llm.seen[2][-1]["content"]
        assert "책1(가)" in recall and "책2(가)" in recall

    async def test_재호출에서_검색이_실패하면_처음_고른_책만_낸다(self):
        ctx = context(
            birth_for(30),
            books=FailsSecondSearch([book(n) for n in range(1, 4)]),
            issued_books=InMemoryIssuedBooks(),
        )
        llm = FakeLLM(
            call("search_books", {"keywords": ["공룡"]}),
            call(
                "propose_books",
                {"items": [pick(1, reason="숫자에 재능이 있어서 골랐어요"), pick(2), pick(3)]},
            ),
            call("search_books", {"keywords": ["기차"]}),
        )
        result = await run(task("book_suggestion"), ctx, client=llm)

        assert result.status == "completed" and result.model_calls == 2
        assert [d.content for d in result.suggestions] == ["책2(가)", "책3(가)"]
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_도서_프롬프트는_도서_구획이고_예시가_없다(self):
        llm = FakeLLM()
        await run(task("book_suggestion"), books_ctx(rows=[book(1)]), client=llm)

        system = llm.seen[0][0]["content"]
        assert "[도서]" in system and "[교육 활동]" not in system
        # [근거] 구획의 글에도 "[예시]" 가 나온다 — 문서 행 예시 블록(머리줄 · 항목)이 없는지 본다
        assert not any(line.startswith("[예시]") for line in system.splitlines())
        assert "- id " not in system
        assert str(CHILD) not in system


class TestObserve:
    """run 하나에 관측 한 줄 — 원문 · 활동 이름이 없다 (growth_plan §11-5)."""

    @staticmethod
    def line(caplog) -> str:
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("growth run ")]
        assert len(lines) == 1
        return lines[0]

    async def test_교육_추천은_단계_근거_모드_문서_행_수를_남기고_원문은_없다(self, caplog):
        doc_id = await example_id()
        caplog.set_level(logging.INFO, logger="app.agents.growth.agent")
        caplog.clear()  # example_id() 가 돌린 run 의 줄은 세지 않는다
        llm = FakeLLM(learning("블록 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id))
        await run(
            task("learning_suggestion", "공룡으로 숫자 놀이 추천해줘"), docs_ctx(), client=llm
        )

        line = self.line(caplog)
        assert "stage=toddler task_type=learning_suggestion status=completed model_calls=1" in line
        assert "evidence_mode=general" in line and "closed_reason=None" in line
        assert re.search(r"doc_hit=[1-9]", line)
        assert "공룡" not in line and "블록 세기" not in line

    async def test_닫히면_코드_문구_키를_남기고_모델은_0회다(self, caplog):
        caplog.set_level(logging.INFO, logger="app.agents.growth.agent")
        await run(task("book_suggestion", "공룡 책"), docs_ctx(), client=FakeLLM())

        line = self.line(caplog)
        assert "status=blocked model_calls=0" in line
        assert "closed_reason=closed.book_api" in line

    async def test_20초에_끊겨도_몇_번째_대화였는지_한_줄을_남긴다(self, caplog):
        class Hangs:
            async def chat(self, messages, **kwargs):
                await asyncio.sleep(10)

        caplog.set_level(logging.INFO, logger="app.agents.growth.agent")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                run(task("learning_suggestion"), docs_ctx(), client=Hangs()), 0.05
            )

        assert "status=cancelled model_calls=1" in self.line(caplog)


def _task(label: str, text: str, *, records: tuple[str, ...] = ()) -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="growth",
        task_type=label,
        request_texts=(text,),
        record_texts=records,
    )


class TestTopic:
    async def test_기록_조각은_요청_다음에_따로_실린다(self):
        llm = FakeLLM()
        ctx = docs_ctx()
        await run(
            _task(
                "learning_suggestion",
                "어떻게 도와줘야 좋을까?",
                records=("아이가 국어 공부를 어려워하는데",),
            ),
            ctx,
            client=llm,
        )
        assert llm.seen[0][1]["content"] == (
            "어떻게 도와줘야 좋을까?\n\n[보호자가 같이 한 말]\n아이가 국어 공부를 어려워하는데"
        )
        assert ctx.state.topic == ("communication",)

    async def test_기록_조각이_없으면_요청만_실린다(self):
        llm = FakeLLM()
        ctx = docs_ctx()
        await run(task("learning_suggestion", "한글 놀이 추천해줘"), ctx, client=llm)
        assert llm.seen[0][1]["content"] == "한글 놀이 추천해줘"
        assert ctx.state.topic == ("communication",)

    async def test_주제가_없는_요청은_주제_키가_비어_있다(self):
        ctx = docs_ctx()
        await run(task("learning_suggestion", "놀이 추천해줘"), ctx, client=FakeLLM())
        assert ctx.state.topic == ()


class TestOffTopicRecall:
    async def test_주제_밖_후보가_빠지면_재호출로_채운다(self):
        text = "한글 놀이 추천해줘"
        probe = FakeLLM()
        await run(task("learning_suggestion", text), docs_ctx(), client=probe)
        doc_id = probe.seen[0][0]["content"].split("- id ", 1)[1].split(" ", 1)[0]
        first = call(
            "propose_learning_activity",
            {
                "items": [
                    {
                        "title": title,
                        "steps": ["하나씩 해 본다"],
                        "materials": [],
                        "evidence": evidence,
                        "reason": "이 나이에 많이 하는 활동이에요",
                    }
                    for title, evidence in (
                        ("공룡 이름 짝맞추기", []),
                        ("한글 자음 찾기", [{"id": doc_id, "note": "이 나이 예시를 참고했어요"}]),
                        (
                            "이야기 이어 말하기",
                            [{"id": doc_id, "note": "이 나이 예시를 참고했어요"}],
                        ),
                    )
                ]
            },
        )
        # 재호출은 후보 세 개를 다시 내고, 앞에서 통과한 둘은 그대로 두고 빈자리만 채운다
        llm = FakeLLM(
            first, learning("글자 카드 놀이", "낱말 이어 말하기", "한글 소리 놀이", doc_id=doc_id)
        )
        result = await run(task("learning_suggestion", text), docs_ctx(), client=llm)
        assert result.status == "completed" and result.model_calls == 2
        titles = [d.content.split("\n")[0] for d in result.suggestions]
        assert titles == ["한글 자음 찾기", "이야기 이어 말하기", "글자 카드 놀이"]
        assert "공룡 이름 짝맞추기" in llm.seen[1][-1]["content"]


class SlowSecondCall:
    """첫 대화는 정해 둔 응답, 둘째 대화부터는 오래 걸리는 모델 가짜."""

    def __init__(self, first: LLMResponse, delay: float) -> None:
        self._first = first
        self._delay = delay
        self.calls = 0

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return self._first
        await asyncio.sleep(self._delay)
        return LLMResponse(
            message=SimpleNamespace(content="끝", tool_calls=None), usage={}, latency_ms=1
        )


def _with_deadline(text: str, seconds: float) -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="growth",
        task_type="learning_suggestion",
        request_texts=(text,),
        deadline=time.perf_counter() + seconds,
    )


class TestRecallDeadline:
    """안전 필터로 빠져 재호출하다 run 마감(20초)에 걸려도 처음 통과한 후보는 지킨다."""

    async def test_재호출이_마감에_걸리면_처음_통과한_후보로_완료한다(self, monkeypatch):
        monkeypatch.setattr("app.agents.growth.agent.RECALL_MARGIN_S", 0.0)
        doc_id = await example_id()
        first = learning("땅콩 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id)
        llm = SlowSecondCall(first, delay=3.0)
        started = time.perf_counter()
        result = await run(_with_deadline("숫자 놀이 추천해줘", 0.5), docs_ctx(), client=llm)
        assert time.perf_counter() - started < 2.0
        assert result.status == "completed" and len(result.suggestions) == 2
        assert [r.code for r in result.readouts] == ["fewer"]

    async def test_재호출_전에_마감이_지났으면_재호출하지_않는다(self):
        doc_id = await example_id()
        llm = FakeLLM(learning("땅콩 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id))
        result = await run(_with_deadline("숫자 놀이 추천해줘", -1.0), docs_ctx(), client=llm)
        assert result.status == "completed" and len(result.suggestions) == 2
        assert result.model_calls == 1 and len(llm.seen) == 1

    async def test_마감이_없으면_지금처럼_재호출한다(self):
        doc_id = await example_id()
        llm = FakeLLM(
            learning("땅콩 세기", "색깔 찾기", "모양 맞추기", doc_id=doc_id),
            learning("색깔 찾기", "소리 듣기", "그림자 놀이", doc_id=doc_id),
        )
        result = await run(
            task("learning_suggestion", "숫자 놀이 추천해줘"), docs_ctx(), client=llm
        )
        assert result.model_calls == 2 and len(result.suggestions) == 3


class TestEvidencePrompt:
    async def test_근거_id_는_한_글자도_바꾸지_말고_그대로_옮기라고_말한다(self):
        llm = FakeLLM()
        await run(task("learning_suggestion", "한글 놀이 추천해줘"), docs_ctx(), client=llm)
        assert "한 글자도 바꾸지 않고 그대로 복사" in llm.seen[0][0]["content"]
