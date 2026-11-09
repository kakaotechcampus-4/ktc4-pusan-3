"""F6 (P1) — 추천 저장이 돌려준 id 수가 추천 수와 다를 때 (eval-v1 §6 · `pipeline.py:2313`).

입력 run 쪽은 이미 판정한다 — test_pipeline.py 의
`test_추천_저장에_실패하면_그_Agent_는_partial_이고_카드가_없다[결과_수가_다름]` (id 가 적은 경우).
남은 것은 같은 `_save` 를 타는 05 다시 만들기(`run_suggestions`)와 id 가 **많은** 경우다.
카드의 id 가 추천과 짝이 안 맞으면 다른 추천을 승인하게 된다 — 그 Agent 는 실패로 센다.
"""

import logging

import pytest

from app.agents import pipeline
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import SuggestionDraft
from app.agents.suggestion_save import SuggestionBatch


class _Ctx:
    def for_task(self) -> "_Ctx":
        return self


def _draft(content: str) -> SuggestionDraft:
    return SuggestionDraft(agent="activity", kind="general", content=content, reason="또래 기준")


@pytest.mark.parametrize("ids", [("s0",), ("s0", "s1", "s2")], ids=["적다", "많다"])
async def test_F6_05_다시_만들기도_저장한_id_수가_다르면_그_Agent_는_실패다(
    ids: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=pipeline.__name__)

    async def ok(task: DomainTask, context: _Ctx) -> DomainAgentResult:
        return DomainAgentResult(
            agent="activity",
            task_type=None,
            status="completed",
            suggestions=(_draft("블록 높이 쌓기"), _draft("그림자 놀이")),
        )

    async def mismatched(batch: SuggestionBatch) -> tuple[str, ...]:
        return ids

    monkeypatch.setattr(pipeline, "_RUNNERS", {"activity": ok})
    task = DomainTask(run_id="req-1", agent="activity", task_type=None, request_texts=("놀이",))

    run = await pipeline.run_suggestions(
        (task,), {"activity": _Ctx()}, save_run_id="run-9", save_suggestions=mismatched
    )

    assert run.outcomes == ()
    assert run.failed == ("activity",)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "추천 저장 결과 수가 다르다" in text
    assert "블록" not in text  # 추천 문장은 로그에 남기지 않는다
