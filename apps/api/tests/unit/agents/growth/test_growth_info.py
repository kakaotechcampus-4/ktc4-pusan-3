"""Growth 일반 정보 질문 (#574) — 추천 없이 growth_doc 근거 답변 하나.

교육과 같은 안전 포트를 쓴다. 아이 월령으로 닫지 않는다 — 질문은 아이 일반이다.
"""

import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.common.doc_answer import DOC_ANSWER_READOUTS
from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import INFO_TASK, DomainTask
from app.agents.growth.agent import run
from app.agents.growth.context import read_learning_safety
from app.agents.growth.info import info_spec
from app.agents.growth.readouts import BLOCKED_SAFETY, READOUTS
from app.agents.growth.store.inmemory import InMemoryGrowthDocs, InMemorySafety
from app.agents.growth.store.ports import GrowthDocRow, SafetyEntry, UnwiredSafety
from tests.unit.agents.growth.support import birth_for, context

BALLOON = GrowthDocRow(
    id=uuid5(NAMESPACE_URL, "info.balloon"),
    doc_key="growth.learn.info.balloon",
    row_type="learning_activity",
    title="풍선 치기",
    body="풍선을 손바닥으로 쳐서 떨어지지 않게 한다.",
    min_month=36,
    max_month=48,
    area="physical_health",
)
COUNT = GrowthDocRow(
    id=uuid5(NAMESPACE_URL, "info.count"),
    doc_key="growth.learn.info.count",
    row_type="learning_activity",
    title="계단 숫자 세기",
    body="계단을 오르며 하나씩 소리 내어 센다.",
    min_month=36,
    max_month=48,
    area="nature",
)


class FakeLLM:
    """행 id 를 읽어 그 행을 단 문장 둘을 낸다."""

    def __init__(self, *, normative: bool = False) -> None:
        self.calls = 0
        self.normative = normative
        self.seen: list[str] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls += 1
        content = messages[-1]["content"]
        self.seen.append(content)
        ids = [part.split(" ", 1)[0] for part in content.split("- id ")[1:]]
        args = {
            "answerable": True,
            "normative_question": self.normative,
            "sentences": [
                {"text": "계단을 오르며 하나씩 소리 내어 세어 봐요.", "row_ids": ids[:1]},
                {"text": "놀이처럼 같이 해 보면 좋아요.", "row_ids": ids[:1]},
            ],
        }
        call = SimpleNamespace(
            id="c1",
            function=SimpleNamespace(
                name="answer_from_docs", arguments=json.dumps(args, ensure_ascii=False)
            ),
        )
        return LLMResponse(
            message=SimpleNamespace(content=None, tool_calls=[call]), usage={}, latency_ms=1
        )


def info(text: str) -> DomainTask:
    return DomainTask(run_id="run-1", agent="growth", task_type=INFO_TASK, request_texts=(text,))


def ctx(months=48, rows=(COUNT, BALLOON), **kwargs):
    return context(birth_for(months), docs=InMemoryGrowthDocs(list(rows)), **kwargs)


async def test_info_는_추천_없이_문서_답변_하나다():
    result = await run(info("보통 3세는 뭐 하고 놀아?"), ctx(), client=FakeLLM())
    assert result.task_type == INFO_TASK and result.suggestions == ()
    assert result.readouts[-1].kind == "doc_answer"
    assert result.readouts[-1].source_refs
    assert all(ref.kind == "growth_doc" for ref in result.readouts[-1].source_refs)
    assert result.model_calls == 1


async def test_info_는_8개월_아이도_닫지_않는다():
    llm = FakeLLM()
    result = await run(info("보통 3세는 숫자 뭐 배워?"), ctx(months=8), client=llm)
    assert result.status != "blocked" and llm.calls == 1


async def test_안전_조회_실패면_blocked_safety_이고_모델_0회():
    llm = FakeLLM()
    result = await run(info("보통 3세는 뭐 해?"), ctx(safety=InMemorySafety(fail=True)), client=llm)
    assert result.readouts == (READOUTS.render(BLOCKED_SAFETY),)
    assert llm.calls == 0 and result.status == "blocked"


async def test_어댑터가_없어도_blocked_safety_다():
    llm = FakeLLM()
    result = await run(info("보통 3세는 뭐 해?"), ctx(safety=UnwiredSafety()), client=llm)
    assert result.readouts == (READOUTS.render(BLOCKED_SAFETY),) and llm.calls == 0


async def test_비식품_알레르기_낱말이_든_행은_보이지_않는다():
    latex = InMemorySafety([SafetyEntry("allergy", "라텍스", "active")])
    c = ctx(safety=latex)
    assert await read_learning_safety(c, consent=True)
    lines = await info_spec(c).rows(36, 48, "풍선 놀이")
    assert lines and all("풍선" not in line.title + line.body for line in lines)


async def test_질문_나이와_겹치는_행만_쓴다():
    c = ctx(months=48)
    assert await read_learning_safety(c, consent=True)
    assert await info_spec(c).rows(12, 24, "보통 1세는 뭐 해?") == []


async def test_진단명_문장은_거절한다():
    c = ctx()
    assert await read_learning_safety(c, consent=True)
    assert info_spec(c).check("자폐 스펙트럼이면 이렇게 해요.", 36) is not None
    assert info_spec(c).check("수준이 높은 아이는 더 해요.", 36) is not None
    assert info_spec(c).check("계단을 오르며 세어 봐요.", 36) is None


async def test_모델_입력에_알레르기_이름이_없다():
    latex = InMemorySafety([SafetyEntry("allergy", "라텍스", "active")])
    llm = FakeLLM()
    await run(info("보통 3세는 뭐 해?"), ctx(safety=latex), client=llm)
    assert llm.seen and "라텍스" not in llm.seen[0]


async def test_행이_없으면_모델_없이_안내다():
    llm = FakeLLM()
    result = await run(info("보통 1세는 뭐 해?"), ctx(), client=llm)
    assert llm.calls == 0
    assert result.readouts == (DOC_ANSWER_READOUTS.render("info.no_rows"),)


async def test_시드_행으로도_답이_나간다():
    llm = FakeLLM()
    c = context(birth_for(48), docs=InMemoryGrowthDocs.from_seed())
    result = await run(info("보통 몇 살부터 혼자 양치해?"), c, client=llm)
    assert llm.calls == 1 and result.readouts[-1].kind == "doc_answer"
    assert UUID(int=0) not in {ref.id for ref in result.readouts[-1].source_refs}


async def test_안전_검사는_질문_나이와_아이_월령_중_어린_쪽으로_본다(monkeypatch):
    """14개월 아이의 보호자가 "보통 4살은 …" 을 물어도 이 아이에게 막히는 위험은 거른다."""
    from app.agents.growth import info as info_module

    seen: list[int] = []
    real = info_module.check_learning_candidate

    def spy(texts, *, months, entries, rules):
        seen.append(months)
        return real(texts, months=months, entries=entries, rules=rules)

    monkeypatch.setattr(info_module, "check_learning_candidate", spy)
    c = ctx(months=14, rows=(COUNT,))
    assert await read_learning_safety(c, consent=True)
    c.state.birth_date = birth_for(14)
    spec = info_spec(c)
    await spec.rows(48, 60, "보통 4살은 뭐 해?")
    spec.check("계단을 오르며 세어 봐요.", 48)
    assert seen and set(seen) == {14}


async def test_시드_행의_출처가_모두_화면_카드로_풀린다():
    """답이 인용한 행은 api 가 describe_docs 로 출처를 만든다 — 풀리지 않으면 계약 위반이다."""
    from app.agents.entrypoint import describe_docs

    llm = FakeLLM()
    c = context(birth_for(48), docs=InMemoryGrowthDocs.from_seed())
    result = await run(info("보통 3세는 뭐 하고 놀아?"), c, client=llm)
    refs = result.readouts[-1].source_refs
    assert refs and len(describe_docs([(r.kind, r.id) for r in refs])) == len(refs)
