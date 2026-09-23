"""백그라운드 러너 — app/api/runs/runner.py

접수 창구는 Agent 를 기다리지 않는다. 러너가 뒤에서 돌면서 채널에 이벤트를 넣고, 끝나면 닫는다.

🚨 껍데기(start)의 약속 하나 — **무슨 일이 있어도 채널은 닫힌다.** job 이 예외로 죽어도 `failed`
   를 내보내고 닫는다. 안 닫히면 화면이 20초 뒤 "결과를 받지 못했어요" 로 떨어지고,
   그때 보호자는 무엇이 저장됐는지 모른다 (use-run-stream.ts 의 unconfirmed).

HTTP 없이 채널과 태스크만 본다.
"""

import asyncio
import uuid

import pytest

from app.api.runs import registry, runner

PARENT = uuid.UUID(int=1)
"""채널은 만든 보호자를 반드시 안다. 여기서는 누구인지가 중요하지 않다."""


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def names(channel: registry.RunChannel) -> list[str]:
    return [name for name, _ in channel.events]


async def test_start_runs_job_attaches_task_and_closes_channel():
    channel = registry.open_run(parent_id=PARENT)

    async def job(ch: registry.RunChannel) -> None:
        ch.publish(("step", {"index": 1, "total": 1, "label": "한 단계"}))
        ch.publish(("done", {"run_id": ch.run_id, "model_calls": 0}))

    task = runner.start(channel, job, raw_text="한 줄")
    await asyncio.wait_for(task, timeout=1)

    assert channel.task is task
    assert channel.closed
    assert names(channel) == ["step", "done"]


async def test_job_exception_ends_with_failed_only_and_channel_closes():
    """계약서 §06 — failed 는 raw_text 를 돌려줘야 "적어주신 말은 입력창에 그대로" 가 성립한다.

    🚨 failed 가 끝 신호다. 뒤에 done 을 붙이지 않는다 — 목(handlers/runs.ts)도 failed 에서 끝나고,
       화면의 `case "done"` 은 상태를 성공으로 덮는다. 둘 다 보내면 프론트 한 줄 차이로 실패가
       "다 됐어요" 가 된다 (#140 리뷰).
    """
    channel = registry.open_run(parent_id=PARENT)

    async def boom(ch: registry.RunChannel) -> None:
        ch.publish(("step", {"index": 1, "total": 3, "label": "시작"}))
        raise RuntimeError("모델이 이상한 걸 뱉었다")

    task = runner.start(channel, boom, raw_text="계란말이 또 찾아요")
    await asyncio.wait_for(task, timeout=1)

    assert names(channel) == ["step", "failed"]
    assert channel.events[1][1] == {"reason": "internal_error", "raw_text": "계란말이 또 찾아요"}
    assert channel.closed


async def test_exception_after_done_keeps_done_as_the_only_end():
    """pipeline 은 done 을 보낸 뒤에도 코드가 더 돈다(결과 기록). 거기서 터져도 화면은 이미
    결과를 받았다 — failed 를 덧붙이지 않는다.
    """
    channel = registry.open_run(parent_id=PARENT)

    async def done_then_boom(ch: registry.RunChannel) -> None:
        ch.publish(("done", {"run_id": ch.run_id, "model_calls": 1}))
        raise RuntimeError("결과 기록 중 실패")

    await asyncio.wait_for(runner.start(channel, done_then_boom, raw_text="한 줄"), timeout=1)

    assert names(channel) == ["done"]
    assert channel.closed


async def test_job_exception_log_keeps_type_and_place_but_not_message(caplog):
    """🚨 로그에 원문을 남기지 않는다 (루트 §2).

    예외 메시지에는 입력 문장이 섞이기 쉽다 — pydantic 검증 에러의 input_value 가 그렇다.
    그래서 예외 종류와 코드 위치만 남기고 메시지는 뺀다.
    """
    channel = registry.open_run(parent_id=PARENT)
    raw_text = "계란말이 또 찾아요"

    async def boom(ch: registry.RunChannel) -> None:
        # 실제 코드처럼 변수에서 메시지를 만든다.
        # 스택에는 소스 줄이 찍히므로 여기 문자열을 직접 쓰면 테스트가 제 발에 걸린다.
        raise ValueError(f"input_value={raw_text!r}")

    await asyncio.wait_for(runner.start(channel, boom, raw_text=raw_text), timeout=1)

    assert "ValueError" in caplog.text
    assert "boom" in caplog.text  # 어디서 터졌는지는 남는다
    assert "계란말이" not in caplog.text


async def test_fake_job_walks_three_steps_then_done():
    """3단계의 가짜 러너. 5단계에서 진짜 pipeline 으로 바뀌지만 껍데기는 그대로 남는다."""
    channel = registry.open_run(parent_id=PARENT)

    await runner.fake_job(channel, step_delay=0.0)

    assert names(channel) == ["step", "step", "step", "done"]
    assert [p["index"] for n, p in channel.events if n == "step"] == [1, 2, 3]
    assert all(p["total"] == 3 for n, p in channel.events if n == "step")
