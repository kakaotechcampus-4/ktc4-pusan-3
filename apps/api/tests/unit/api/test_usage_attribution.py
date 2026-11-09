"""LLM 사용량 귀속 — 사진 run · 기록 삭제 · 대화 삭제 확정 (#710).

모델을 부르는 창구마다 `llm_usage.attribute()` 를 건다.
안 걸면 서버에서 그 호출의 토큰이 보호자 없이 쓰여 저장되지 않고 "귀속 없음" WARNING 만 남는다.
기록 삭제 · 삭제 확정 길의 라우트 연결은 integration/api 의 삭제 테스트가 본다.
입력 run 은 test_runner.py, 05 다시 만들기는 integration/api/test_suggestions_endpoint.py 가 본다.

모델은 부르지 않는다. 창구가 부르는 진입점을 가짜로 바꿔 끼워, 그 안에서 보이는 귀속만 본다.
"""

import asyncio
import logging
import re
import uuid
from datetime import UTC, date, datetime

import pytest

from app.agents import entrypoint
from app.api.runs import photo_run, registry, runner
from app.core import llm_usage

PARENT = uuid.UUID(int=1)
CHILD = uuid.UUID(int=2)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    photo_run._runs.clear()
    yield
    registry.clear()
    photo_run._runs.clear()


async def test_사진_run_은_photo_로_귀속한다(monkeypatch):
    seen: list[llm_usage.Attribution | None] = []

    def fake_read_photo(data, *, received_at):
        # asyncio.to_thread 로 스레드에서 불린다 — 귀속이 스레드까지 넘어와야 한다
        seen.append(llm_usage.current())
        raise entrypoint.UnreadablePhoto("synthetic")

    monkeypatch.setattr(entrypoint, "read_photo", fake_read_photo)
    channel = registry.open_run(parent_id=PARENT)
    run = photo_run.open_photo_run(run_id=channel.run_id, parent_id=PARENT, child_id=CHILD)
    job = photo_run.photo_job(
        run, data=b"synthetic", received_at=datetime.now(UTC), declared_lane="notice"
    )

    await asyncio.wait_for(runner.start(channel, job, raw_text=""), timeout=1)

    assert seen == [llm_usage.Attribution(parent_id=PARENT, run_id=channel.run_id, source="photo")]


def test_삭제_뒤_Curator_는_delete_로_귀속한다(monkeypatch):
    seen: list[llm_usage.Attribution | None] = []

    def fake_trigger(child_id, today):
        # 실제 trigger 는 여기서 create_task 로 Curator 를 띄운다 — 그 태스크가 이 귀속을 복사한다
        seen.append(llm_usage.current())

    monkeypatch.setattr(runner, "curator_trigger", lambda: fake_trigger)

    runner.trigger_curator_after_delete(
        CHILD, date(2026, 11, 9), parent_id=PARENT, after="기록 삭제"
    )

    [attribution] = seen
    assert attribution is not None
    assert (attribution.parent_id, attribution.source) == (PARENT, "delete")
    assert re.fullmatch(r"[0-9a-f]{32}", attribution.run_id)  # registry 의 run_id 와 같은 모양


def test_삭제_뒤_trigger_가_터져도_응답은_성공이고_귀속은_풀린다(monkeypatch, caplog):
    def boom(child_id, today):
        raise RuntimeError("synthetic-secret")

    monkeypatch.setattr(runner, "curator_trigger", lambda: boom)

    with caplog.at_level(logging.ERROR, logger="app.api.runs.runner"):
        runner.trigger_curator_after_delete(
            CHILD, date(2026, 11, 9), parent_id=PARENT, after="삭제 확정"
        )  # 지운 것은 이미 commit 됐다 — 예외를 내지 않는다

    assert llm_usage.current() is None
    [record] = caplog.records
    # 어느 길인지와 예외 종류만 남는다 — 메시지는 싣지 않는다 (루트 §2)
    assert "삭제 확정" in record.getMessage()
    assert "RuntimeError" in record.getMessage()
    assert "synthetic-secret" not in caplog.text


def test_임베딩_키가_없어_trigger_가_없으면_아무것도_하지_않는다(monkeypatch):
    monkeypatch.setattr(runner, "curator_trigger", lambda: None)

    runner.trigger_curator_after_delete(
        CHILD, date(2026, 11, 9), parent_id=PARENT, after="기록 삭제"
    )
