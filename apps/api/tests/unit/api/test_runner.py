"""백그라운드 러너 — app/api/runs/runner.py

접수 창구는 Agent 를 기다리지 않는다. 러너가 뒤에서 돌면서 채널에 이벤트를 넣고, 끝나면 닫는다.

🚨 껍데기(start)의 약속 하나 — **무슨 일이 있어도 채널은 닫힌다.** job 이 예외로 죽어도 `failed`
   를 내보내고 닫는다. 안 닫히면 화면이 20초 뒤 "결과를 받지 못했어요" 로 떨어지고,
   그때 보호자는 무엇이 저장됐는지 모른다 (use-run-stream.ts 의 unconfirmed).

HTTP 없이 채널과 태스크만 본다.
"""

import asyncio
import uuid
from datetime import date

import pytest

from app.agents import entrypoint
from app.agents.entrypoint import (
    Done,
    EventDrafts,
    Failed,
    MemoryNote,
    Partial,
    PendingMemoryContext,
    PendingReply,
    Step,
    Unwritten,
)
from app.agents.memory.schemas.task import WorkType
from app.api import idempotency
from app.api.runs import pending_reply, registry, runner

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
        emit(Unwritten(hints=1, tools=0, note=False))  # 번역기가 보내지 않는 것
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
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["step", "failed"]
    assert channel.events[-1][1] == {"reason": "timeout", "raw_text": RAW_TEXT}
    assert idempotency.recall(**scope) is None


async def test_job_that_returns_without_an_end_signal_ends_with_failed():
    """러너의 약속 — 무슨 일이 있어도 끝 신호는 하나다. 끝 신호 없이 돌아온 job 에 failed 를 붙인다.

    안 붙이면 화면은 "확인하지 못했어요" 로 떨어지고 키도 안 풀려서, 다시 눌러도 닫힌 run 만
    재생된다. Memory 단계 전이라 commit 이 안 됐으므로 이 failed 는 "저장 없음" 이 맞다.
    """
    channel = registry.open_run(parent_id=PARENT)

    async def quiet(ch: registry.RunChannel) -> None:
        ch.publish(("step", {"index": 1, "total": 3, "label": "시작"}))

    await asyncio.wait_for(runner.start(channel, quiet, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["step", "failed"]
    assert channel.events[-1][1] == {"reason": "internal_error", "raw_text": RAW_TEXT}


async def test_untranslatable_event_fails_the_run_loudly(monkeypatch, caplog):
    """번역이 터지면 run 전체가 failed 로 끝난다 — 조용히 빠지지 않고 화면에 드러난다.

    그래도 데이터는 안전하다. Memory 단계가 끝나면 commit 하고, commit 이후에 터져도
    저장된 관찰은 되돌리지 않는다. commit 된 run 은 done 으로 끝내고 키를 놓지 않아
    다시 보내도 두 번 저장되지 않는다.
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
    idempotency.remember(**scope, replay=channel.run_id)

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
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["failed"]
    assert idempotency.recall(**scope) is None


async def test_부분_결과는_done_으로_끝나고_키를_놓지_않는다(monkeypatch):
    """추천 일부가 빠져도 기록은 저장됐다. 키를 놓으면 다시 눌렀을 때 두 번 저장된다."""

    async def fake_handle_input(**kwargs):
        emit = kwargs["emit"]
        emit(Step(3, 3, "다음 행동을 준비하고 있어요"))
        emit(Partial("timeout_20s", ("activity",), ("food",)))
        emit(Done(kwargs["run_id"], 3))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k1"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["step", "partial", "done"]
    assert channel.ended_with == "done"
    assert idempotency.recall(**scope) == channel.run_id


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


_PENDING = PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)


async def test_되묻기로_끝난_run_의_맥락은_채널이_아니라_store_로_간다(monkeypatch):
    # 조각 원문이 들어 있다. 화면으로 흘리지 않고 다음 입력의 reply_to 가 찾을 곳에 둔다
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        emit = kwargs["emit"]
        emit(MemoryNote("언제부터였어요?", "question"))
        emit(PendingReply(kwargs["run_id"], _PENDING))
        emit(Done(kwargs["run_id"], 2))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["note", "done"]
    stored = pending_reply.consume(run_id=channel.run_id, parent_id=PARENT, child_id=CHILD)
    assert stored == _PENDING
    pending_reply.clear()


async def test_이어받기_맥락을_진입점에_넘긴다(monkeypatch):
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD, parent_id=PARENT, raw_text="3일 전부터", continuation=_PENDING
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=1)

    assert seen["continuation"] is _PENDING


async def test_아이_생일을_진입점에_넘긴다(monkeypatch):
    """9단계 — 창구가 확인한 아이의 생일로 식이 단계를 정한다 (09-23 결정, 12개월 경계).

    안 넘기면 진입점이 "만 2세" 기본값을 써서 12개월 미만 아기도 유아 단계가 된다.
    """
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT, birth_date=date(2025, 11, 1)
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert seen["birth_date"] == date(2025, 11, 1)


async def test_이어받기_run_이_실패하면_맥락을_되돌려_둔다(monkeypatch):
    # 창구가 맥락을 꺼낸 뒤 Agent 가 실패하면 "다시 시도" 가 400 을 받는다.
    # 되돌려 둬야 같은 답으로 재시도가 된다
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        kwargs["emit"](Failed("llm_unavailable", "3일 전부터"))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        continuation=_PENDING,
        reply_to="r-prev",
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=1)

    assert pending_reply.consume(run_id="r-prev", parent_id=PARENT, child_id=CHILD) == _PENDING
    pending_reply.clear()


async def test_이어받기_run_이_끝나면_맥락을_되돌리지_않는다(monkeypatch):
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        continuation=_PENDING,
        reply_to="r-prev",
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=1)

    assert pending_reply.consume(run_id="r-prev", parent_id=PARENT, child_id=CHILD) is None


async def test_commit_후_예외가_나도_키를_풀지_않는다(monkeypatch):
    """Memory 단계에서 commit 이 됐는데 그 뒤(번역·도메인 Agent 등)에서 터지면,
    _guarded 는 done 으로 끝내고 키를 놓지 않는다 — 놓으면 재시도가 두 번 저장한다.
    """

    async def fake_handle_input(**kwargs):
        kwargs["emit"](Step(1, 3, "입력을 살펴보고 있어요"))
        kwargs["store"].wrote = True  # Memory Agent 가 관찰을 저장한 것으로 표시
        await kwargs["commit"]()  # Memory commit
        raise RuntimeError("commit 뒤에 터졌다")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-commit"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    # commit 된 run 은 done 으로 끝난다
    assert channel.ended_with == "done"
    assert names(channel) == ["step", "done"]
    # 키가 풀리지 않는다 — 같은 키로 재시도하면 기존 run 이 재생된다
    assert idempotency.recall(**scope) == channel.run_id
    assert channel.closed


async def test_이어받기_commit_후_예외가_나도_맥락을_복구하지_않는다(monkeypatch):
    """이어받기 run 에서 commit 뒤 터지면 맥락을 되돌리지 않는다.
    이미 답이 저장됐으니 맥락을 복구하면 같은 답을 다시 보내게 된다.
    """
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        kwargs["store"].wrote = True
        await kwargs["commit"]()
        raise RuntimeError("commit 뒤에 터졌다")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        continuation=_PENDING,
        reply_to="r-prev",
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=1)

    # commit 됐으므로 done 으로 끝남
    assert channel.ended_with == "done"
    # 맥락이 복구되지 않음 — 이미 저장됐으니 다시 보낼 필요 없음
    assert pending_reply.consume(run_id="r-prev", parent_id=PARENT, child_id=CHILD) is None
    pending_reply.clear()


async def test_이어받기_run_이_예외로_죽어도_맥락을_되돌려_둔다(monkeypatch):
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        continuation=_PENDING,
        reply_to="r-prev",
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=1)

    assert channel.ended_with == "failed"
    assert pending_reply.consume(run_id="r-prev", parent_id=PARENT, child_id=CHILD) == _PENDING
    pending_reply.clear()


async def test_timeout이_commit_뒤에_걸려도_done으로_끝난다(monkeypatch):
    """60초 안전망이 commit 뒤에 걸리면 failed가 아니라 done으로 끝나고 키를 안 풀어야 한다."""

    async def fake_handle_input(**kwargs):
        kwargs["store"].wrote = True
        await kwargs["commit"]()
        await asyncio.sleep(999)  # timeout 유발

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.1)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-timeout-commit"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=2)

    assert channel.ended_with == "done"
    # 키가 풀리지 않는다 — commit 된 run 이니까
    assert idempotency.recall(**scope) == channel.run_id
    assert channel.closed


async def test_저장_안_한_run의_commit_후_예외는_failed로_끝난다(monkeypatch):
    """되묻기만 한 run에서 commit 후 터지면 wrote=False라 failed로 끝나야 한다.
    키가 풀려야 보호자가 다시 보낼 수 있다."""

    async def fake_handle_input(**kwargs):
        # store.wrote = False (저장 안 함)
        await kwargs["commit"]()
        raise RuntimeError("commit 뒤에 터졌다")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-no-write"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert channel.ended_with == "failed"
    # 키가 풀려야 한다 — 저장한 게 없으니 재시도해도 안전하다
    assert idempotency.recall(**scope) is None
