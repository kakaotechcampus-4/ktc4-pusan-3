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

from app.agents import entrypoint
from app.agents.entrypoint import Done, EventDrafts, Failed, MemoryNote, Step
from app.api import idempotency
from app.api.runs import registry, runner

PARENT = uuid.UUID(int=1)
"""채널은 만든 보호자를 반드시 안다. 여기서는 누구인지가 중요하지 않다."""
CHILD = uuid.UUID(int=2)
RAW_TEXT = "계란말이 또 찾아요"


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    idempotency.clear()
    yield
    registry.clear()
    idempotency.clear()


def names(channel: registry.RunChannel) -> list[str]:
    return [name for name, _ in channel.events]


async def test_agent_job_calls_the_agents_entrypoint_and_relays_its_events(monkeypatch):
    """5단계 — 러너가 진짜 Agent(entrypoint.handle_input)를 부르고, 진행 이벤트를 번역해 넣는다.

    LLM 은 부르지 않는다. 진입점을 가짜로 바꿔 끼워 넘어가는 값과 채널에 쌓이는 것만 본다.
    """
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        emit = kwargs["emit"]
        emit(Step(1, 3, "입력을 살펴보고 있어요"))
        emit(MemoryNote("기록해 둘게요"))  # 번역기가 보내지 않는 것
        emit(Done(kwargs["run_id"], 2))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert seen["child_id"] == CHILD
    assert seen["parent_id"] == PARENT  # 보호자 발화가 아이 것으로 저장되지 않게 (§2)
    assert seen["raw_text"] == RAW_TEXT
    assert seen["run_id"] == channel.run_id  # 202 로 준 run_id 가 done 까지 같은 값
    assert names(channel) == ["step", "done"]
    assert channel.closed


async def test_agent_job_stops_a_run_past_the_deadline(monkeypatch):
    """안전망 — 모델이 멈춘 run 을 끊는다. 서버가 안 끊으면 ping 이 화면의 20초 타이머를 계속
    되살려서 진행 화면이 몇 분씩 돈다.

    끊긴 run 은 failed(timeout) 으로 끝나고 키를 놓는다 — "다시 시도" 가 새 run 을 띄운다.
    """

    async def stuck_handle_input(**kwargs):
        kwargs["emit"](Step(1, 3, "입력을 살펴보고 있어요"))
        await asyncio.sleep(10)  # 모델이 멈춘 것처럼

    monkeypatch.setattr(entrypoint, "handle_input", stuck_handle_input)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.05)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k1"}
    idempotency.remember(**scope, run_id=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["step", "failed"]
    assert channel.events[-1][1] == {"reason": "timeout", "raw_text": RAW_TEXT}
    assert idempotency.recall(**scope) is None


async def test_job_that_returns_without_an_end_signal_ends_with_failed():
    """러너의 약속 — 무슨 일이 있어도 끝 신호는 하나다. 끝 신호 없이 돌아온 job 에 failed 를 붙인다.

    안 붙이면 화면은 "확인하지 못했어요" 로 떨어지고 키도 안 풀려서, 다시 눌러도 닫힌 run 만
    재생된다. 7단계에서 러너 세션은 done 일 때만 확정하므로 이 failed 는 "저장 없음" 이 맞다.
    """
    channel = registry.open_run(parent_id=PARENT)

    async def quiet(ch: registry.RunChannel) -> None:
        ch.publish(("step", {"index": 1, "total": 3, "label": "시작"}))

    await asyncio.wait_for(runner.start(channel, quiet, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["step", "failed"]
    assert channel.events[-1][1] == {"reason": "internal_error", "raw_text": RAW_TEXT}


async def test_untranslatable_event_fails_the_run_loudly(monkeypatch, caplog):
    """번역이 터지면 run 전체가 failed 로 끝난다 — 조용히 빠지지 않고 화면에 드러난다.

    그래도 데이터는 안전하다. 러너 세션은 done 일 때만 확정하고 나머지는 되돌리므로(7단계)
    failed 는 "저장 없음" 이고, 키가 풀려 다시 보내도 두 번 저장되지 않는다.
    로그에는 예외 종류와 위치만 남고 원문은 남지 않는다 (루트 §2).
    """
    secret = RAW_TEXT

    class BrokenDraft:
        def to_payload(self) -> dict:
            raise ValueError(f"input_value={secret!r}")

    async def fake_handle_input(**kwargs):
        kwargs["emit"](Step(1, 3, "입력을 살펴보고 있어요"))
        kwargs["emit"](EventDrafts((BrokenDraft(),)))
        kwargs["emit"](Done(kwargs["run_id"], 2))  # 위에서 터져서 여기까지 안 온다

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k1"}
    idempotency.remember(**scope, run_id=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["step", "failed"]
    assert channel.events[-1][1] == {"reason": "internal_error", "raw_text": RAW_TEXT}
    assert idempotency.recall(**scope) is None
    assert "ValueError" in caplog.text
    assert secret not in caplog.text


async def test_failed_from_the_agents_releases_the_key(monkeypatch):
    """Agent 가 스스로 알린 실패(예외가 아니라 Failed 이벤트)도 키를 놓는다.

    LLM 키가 없거나 모델이 죽으면 pipeline 은 예외 없이 Failed("llm_unavailable") → Done 을 보낸다.
    그때도 "다시 시도" 가 새 run 을 띄워야 한다 — 저장된 것이 없는 실패다.
    """

    async def fake_handle_input(**kwargs):
        kwargs["emit"](Failed("llm_unavailable", kwargs["raw_text"]))
        kwargs["emit"](Done(kwargs["run_id"], 0))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k1"}
    idempotency.remember(**scope, run_id=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["failed"]
    assert idempotency.recall(**scope) is None


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
