"""일반 정보 질문의 문서 근거 답변 뼈대 (#574).

가짜 Spec 과 가짜 모델로 순서를 고정한다 — 안전 실패 → 나이 구간 → 행 → 모델 1회 → 검사 → readout.
"""

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.agents.common.doc_answer import (
    DOC_ANSWER_READOUTS,
    INFO_ROW_LIMIT,
    DocAnswerSpec,
    DocLine,
    answer_from_docs,
)
from app.agents.common.llm_client import LLMResponse, LLMUnavailableError
from app.agents.common.readout import Readout
from app.agents.common.schemas.task import INFO_TASK, DomainTask

LIMIT = 72
BLOCKED = Readout(kind="blocked", body="안전 정보를 확인하지 못했어요.", authored_by="code")


def line(n: int, title: str, body: str = "같이 해 본다.") -> DocLine:
    return DocLine(id=UUID(int=n), title=title, body=body)


ROWS = (
    line(1, "숫자 블록 세기", "블록을 1부터 5까지 같이 센다."),
    line(2, "양치 노래", "노래에 맞춰 이를 닦는다."),
    line(3, "그림책 읽기", "그림을 보며 이야기를 나눈다."),
)


class FakeRows:
    def __init__(self, rows=ROWS) -> None:
        self.rows = tuple(rows)
        self.asked: list[tuple[int, int]] = []

    async def __call__(self, lo: int, hi: int, question: str):
        self.asked.append((lo, hi))
        return self.rows


def spec(rows=None, check=None) -> DocAnswerSpec:
    return DocAnswerSpec(
        agent="growth",
        doc_kind="growth_doc",
        topic="생활과 배움",
        rows=rows or FakeRows(),
        check=check or (lambda sentence, lo: None),
        blocked=BLOCKED,
    )


def task(text: str) -> DomainTask:
    return DomainTask(run_id="run-1", agent="growth", task_type=INFO_TASK, request_texts=(text,))


class FakeLLM:
    def __init__(self, args: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.args = args
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls.append({"messages": messages, **kwargs})
        if self.fail:
            raise LLMUnavailableError("끊김")
        tool_calls = None
        if self.args is not None:
            tool_calls = [
                SimpleNamespace(
                    id="c1",
                    function=SimpleNamespace(
                        name="answer_from_docs",
                        arguments=json.dumps(self.args, ensure_ascii=False),
                    ),
                )
            ]
        return LLMResponse(
            message=SimpleNamespace(content=None, tool_calls=tool_calls), usage={}, latency_ms=1
        )


def answer(*sentences: tuple[str, list[int]], answerable=True, normative=False) -> dict:
    return {
        "answerable": answerable,
        "normative_question": normative,
        "sentences": [
            {"text": t, "row_ids": [str(UUID(int=n)) for n in ids]} for t, ids in sentences
        ],
    }


async def run(text: str, llm: FakeLLM, *, s=None, months=48, safety_ok=True):
    return await answer_from_docs(
        s or spec(),
        task(text),
        child_months=months,
        month_limit=LIMIT,
        safety_ok=safety_ok,
        llm=llm,
    )


async def test_안전_조회가_실패하면_그_Agent_의_blocked_이고_모델_0회():
    llm = FakeLLM(answer(("블록을 같이 세요.", [1])))
    result = await run("보통 3세는 뭐 해?", llm, safety_ok=False)
    assert result.status == "blocked" and result.readouts == (BLOCKED,)
    assert llm.calls == [] and result.model_calls == 0 and result.suggestions == ()


async def test_질문_속_나이로_행을_고른다_아이_나이가_아니다():
    rows = FakeRows()
    await run(
        "보통 1세는 뭐 해?", FakeLLM(answer(("블록을 같이 세요.", [1]))), s=spec(rows), months=48
    )
    assert rows.asked == [(12, 24)]


async def test_나이가_없으면_아이_월령_한_달이다():
    rows = FakeRows()
    await run(
        "또래 애들은 뭐 해?", FakeLLM(answer(("블록을 같이 세요.", [1]))), s=spec(rows), months=30
    )
    assert rows.asked == [(30, 31)]


async def test_구간이_상한을_넘으면_자른다():
    rows = FakeRows()
    await run("보통 5~7세는 뭐 해?", FakeLLM(answer(("블록을 같이 세요.", [1]))), s=spec(rows))
    assert rows.asked == [(60, LIMIT)]


@pytest.mark.parametrize("text", ["보통 7세는 뭐 해?", "4~3세 애들은 뭐 해?"])
async def test_범위_밖이면_모델_없이_안내다(text):
    llm = FakeLLM(answer(("블록을 같이 세요.", [1])))
    result = await run(text, llm)
    assert llm.calls == [] and result.model_calls == 0
    assert result.readouts == (DOC_ANSWER_READOUTS.render("info.out_of_range"),)


async def test_행이_없으면_모델_없이_안내다():
    llm = FakeLLM()
    result = await run("보통 3세는 뭐 해?", llm, s=spec(FakeRows(())))
    assert llm.calls == [] and result.readouts == (DOC_ANSWER_READOUTS.render("info.no_rows"),)


async def test_모델에는_최대_5행만_간다():
    many = [line(n, f"활동 {n}") for n in range(1, 10)]
    llm = FakeLLM(answer(("활동을 같이 해요.", [1])))
    await run("보통 3세는 뭐 해?", llm, s=spec(FakeRows(many)))
    shown = llm.calls[0]["messages"][-1]["content"]
    assert sum(f"id {UUID(int=n)}" in shown for n in range(1, 10)) == INFO_ROW_LIMIT


async def test_질문과_겹치는_낱말이_많은_행이_먼저다():
    many = [line(n, f"활동 {n}") for n in range(1, 8)] + [line(9, "양치 노래 부르기")]
    llm = FakeLLM(answer(("노래에 맞춰 닦아요.", [9])))
    await run("보통 몇 살부터 혼자 양치해?", llm, s=spec(FakeRows(many)))
    assert f"id {UUID(int=9)}" in llm.calls[0]["messages"][-1]["content"]


async def test_답은_readout_하나이고_근거는_단_행이다():
    llm = FakeLLM(
        answer(("블록을 1부터 5까지 같이 세요.", [1]), ("노래에 맞춰 이를 닦아요.", [2, 1]))
    )
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.status == "completed" and result.suggestions == () and result.model_calls == 1
    [readout] = result.readouts
    assert readout.kind == "doc_answer" and readout.authored_by == "model"
    assert readout.title == "만 3세 무렵 생활과 배움 · 일반 자료"
    assert readout.body == "블록을 1부터 5까지 같이 세요. 노래에 맞춰 이를 닦아요."
    assert [(r.kind, r.id) for r in readout.source_refs] == [
        ("growth_doc", UUID(int=1)),
        ("growth_doc", UUID(int=2)),
    ]


async def test_모델은_한_번만_부르고_출력_tool_을_강제한다():
    llm = FakeLLM(answer(("블록을 같이 세요.", [1])))
    await run("보통 3세는 뭐 해?", llm)
    assert len(llm.calls) == 1
    assert llm.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "answer_from_docs"},
    }


async def test_모델_입력에_아이_나이가_없다():
    llm = FakeLLM(answer(("블록을 같이 세요.", [1])))
    await run("또래 애들은 뭐 해?", llm, months=30)
    text = "\n".join(m["content"] for m in llm.calls[0]["messages"])
    assert "30개월" not in text


async def test_answerable_false_면_모델_문장을_버리고_안내다():
    llm = FakeLLM(answer(("모르겠지만 이럴 거예요.", [1]), answerable=False))
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.readouts == (DOC_ANSWER_READOUTS.render("info.no_match"),)


async def test_기준_질문이면_코드_안내가_답_앞에_붙는다():
    llm = FakeLLM(answer(("블록을 1부터 5까지 같이 세요.", [1]), normative=True))
    result = await run("보통 3세는 숫자 몇까지 세?", llm)
    assert result.readouts[0] == DOC_ANSWER_READOUTS.render("info.norm_notice")
    assert result.readouts[0].source_refs == ()
    assert result.readouts[1].kind == "doc_answer"


@pytest.mark.parametrize(
    "sentence",
    [
        "또래보다 빨리 세요.",
        "우리 아이는 블록을 잘 세요.",
        "우리 애도 그래요.",
        "우리 아기에게는 계단 세기를 해 보세요.",
        "우리 딸은 좋아할 거예요.",
        "저희 아이도 해 봤어요.",
    ],
)
async def test_평가_표현이나_우리_아이가_있으면_답_전체를_버린다(sentence):
    llm = FakeLLM(answer(("블록을 1부터 5까지 같이 세요.", [1]), (sentence, [1])))
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.status == "degraded"
    assert result.readouts == (DOC_ANSWER_READOUTS.render("info.fallback"),)


async def test_단_행에_없는_숫자를_쓴_문장은_뺀다():
    llm = FakeLLM(answer(("블록을 1부터 5까지 같이 세요.", [1]), ("보통 10까지 세요.", [1])))
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.readouts[-1].body == "블록을 1부터 5까지 같이 세요."


async def test_질문에_있는_숫자는_써도_된다():
    llm = FakeLLM(answer(("3세 무렵에는 블록을 같이 세요.", [1])))
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.readouts[-1].body == "3세 무렵에는 블록을 같이 세요."


async def test_보여_주지_않은_id_나_인용이_없는_문장은_뺀다():
    llm = FakeLLM(
        answer(("블록을 같이 세요.", [1]), ("지어낸 자료예요.", [77]), ("근거가 없어요.", []))
    )
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.readouts[-1].body == "블록을 같이 세요."
    assert [r.id for r in result.readouts[-1].source_refs] == [UUID(int=1)]


async def test_도메인_검사에_걸린_문장은_뺀다():
    def check(sentence: str, lo: int) -> str | None:
        return "위험" if "땅콩" in sentence else None

    llm = FakeLLM(answer(("블록을 같이 세요.", [1]), ("땅콩을 세어 봐요.", [1])))
    result = await run("보통 3세는 뭐 해?", llm, s=spec(check=check))
    assert result.readouts[-1].body == "블록을 같이 세요."


async def test_남는_문장이_없으면_고정_안내로_닫는다():
    llm = FakeLLM(answer(("지어낸 자료예요.", [77])))
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.status == "degraded"
    assert result.readouts == (DOC_ANSWER_READOUTS.render("info.fallback"),)


@pytest.mark.parametrize("llm", [FakeLLM(fail=True), FakeLLM(None)])
async def test_모델이_실패하거나_tool_을_안_부르면_다시_부르지_않고_고정_안내(llm):
    result = await run("보통 3세는 뭐 해?", llm)
    assert len(llm.calls) == 1 and result.model_calls == 1
    assert result.status == "degraded"
    assert result.readouts == (DOC_ANSWER_READOUTS.render("info.fallback"),)
    assert result.readouts[0].source_refs == ()


async def test_인자가_스키마와_다르면_고정_안내():
    llm = FakeLLM({"answerable": "몰라"})
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.status == "degraded"


def test_고정_문구는_모두_코드_문구다():
    for key in (
        "info.no_rows",
        "info.no_match",
        "info.out_of_range",
        "info.fallback",
        "info.norm_notice",
    ):
        readout = DOC_ANSWER_READOUTS.render(key)
        assert readout.authored_by == "code" and readout.body.strip()


async def test_문장_하나가_너무_길거나_근거가_많아도_그_문장만_뺀다():
    long_text = "블록을 같이 세어 봐요. " * 12
    llm = FakeLLM(
        {
            "answerable": True,
            "normative_question": False,
            "sentences": [
                {"text": "블록을 1부터 5까지 같이 세요.", "row_ids": [str(UUID(int=1))]},
                {"text": long_text, "row_ids": [str(UUID(int=1))]},
                {
                    "text": "노래에 맞춰 이를 닦아요.",
                    "row_ids": [str(UUID(int=n)) for n in (1, 2, 3, 1)],
                },
            ],
        }
    )
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.status == "completed"
    assert result.readouts[-1].body == "블록을 1부터 5까지 같이 세요."


async def test_문장이_네_개를_넘어도_앞의_네_개로_답한다():
    words = ("하나", "둘", "셋", "넷", "다섯", "여섯")
    sentences = [(f"블록을 같이 세요 {word}.", [1]) for word in words]
    llm = FakeLLM(
        {
            "answerable": True,
            "normative_question": False,
            "sentences": [
                {"text": t, "row_ids": [str(UUID(int=n)) for n in ids]} for t, ids in sentences
            ],
        }
    )
    result = await run("보통 3세는 뭐 해?", llm)
    assert result.status == "completed"
    assert result.readouts[-1].body.count("블록을") == 4


async def test_질문에_있는_기준_숫자를_그대로_확인해_주는_문장은_뺀다():
    """보호자가 "10까지" 라고 물어도 모델이 "10까지 셀 수 있어요" 로 기준을 확인해 주면 안 된다."""
    llm = FakeLLM(
        answer(("3세면 10까지 셀 수 있어요.", [1]), ("블록을 1부터 5까지 같이 세요.", [1]))
    )
    result = await run("보통 3세는 숫자 10까지 세?", llm)
    assert result.readouts[-1].body == "블록을 1부터 5까지 같이 세요."


async def test_질문의_나이_숫자는_나이로_쓸_때만_허용한다():
    llm = FakeLLM(answer(("만 3세 무렵에는 블록을 같이 세요.", [1])))
    result = await run("보통 3세는 숫자 10까지 세?", llm)
    assert result.readouts[-1].body == "만 3세 무렵에는 블록을 같이 세요."


def test_고정_문구는_글자_그대로다():
    expected = {
        "info.out_of_range": "지금은 만 5세까지의 일반 자료로만 답할 수 있어요.",
        "info.no_rows": "이 나이에 맞는 일반 자료가 아직 없어요.",
        "info.no_match": "이 질문에 맞는 자료가 아직 없어요.",
        "info.fallback": "자료로 답을 만들지 못했어요. 잠시 뒤 다시 물어봐 주세요.",
        "info.norm_notice": (
            "아이마다 속도가 달라서 나이별 기준은 알려드리지 않아요. "
            "발달이 걱정되면 영유아 건강검진에서 확인해 보세요."
        ),
    }
    for key, body in expected.items():
        assert DOC_ANSWER_READOUTS.render(key).body == body
