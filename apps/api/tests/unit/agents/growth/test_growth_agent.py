"""Growth run() — 닫힘(모델 0) · 리듬 안내(모델 0) · tool 루프 · 재호출 1회 · 결과 봉투.

모델은 정해 둔 응답을 순서대로 내는 가짜다 (Activity test_activity_agent.py 와 같은 모양).
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from app.agents.common.llm_client import LLMResponse
from app.agents.growth.agent import run
from app.agents.growth.store.inmemory import (
    InMemoryGrowthDocs,
    InMemoryGrowthMemory,
    InMemorySafety,
)
from app.agents.growth.store.ports import RoutineObservation, SafetyEntry
from app.agents.growth.tools.docs import match_chain, routine_chains
from app.agents.growth.tools.routine import pick_next_step
from tests.unit.agents.growth.support import CHILD, TODAY, birth_for, context, task


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
    return context(birth_for(months), docs=InMemoryGrowthDocs.from_seed(), **kwargs)


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

    async def test_말로_끝내면_failed_다(self):
        result = await run(task("learning_suggestion"), docs_ctx(), client=FakeLLM())
        assert result.status == "failed" and result.suggestions == ()

    async def test_모델_입력에_등록한_알레르기_이름이_없다(self):
        safety = InMemorySafety([SafetyEntry(kind="allergy", label="라텍스", status="active")])
        llm = FakeLLM()
        await run(task("learning_suggestion"), docs_ctx(safety=safety), client=llm)
        assert "라텍스" not in json.dumps(llm.seen, ensure_ascii=False)


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
                {"category": "self_care", "subject": "양치", "items": list(items)},
            )

        llm = FakeLLM(
            call("search_routine_memory", {}),
            propose(item("양치", 0, "어른 손을 포개요"), item("손 씻기", 1, "땅콩 크림을 발라요")),
            propose(item("손 씻기", 1, "비누 거품을 같이 내요")),
        )
        result = await run(task("routine_coaching", "양치 혼자 하게"), ctx, client=llm)
        assert result.model_calls == 2 and len(result.suggestions) == 2
        assert [r.code for r in result.readouts] == ["fewer"]  # 단계가 둘뿐이라 개수 안내가 붙는다


class TestBook:
    async def test_도서_포트가_없으면_닫힌다(self):
        result = await run(task("book_suggestion", "공룡 책"), docs_ctx(), client=FakeLLM())
        assert result.status == "blocked" and result.model_calls == 0
