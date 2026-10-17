"""백그라운드 러너 — app/api/runs/runner.py

접수 창구는 Agent 를 기다리지 않는다. 러너가 뒤에서 돌면서 채널에 이벤트를 넣고, 끝나면 닫는다.

🚨 껍데기(start)의 약속 하나 — **무슨 일이 있어도 채널은 닫힌다.** job 이 예외로 죽어도 `failed`
   를 내보내고 닫는다. 안 닫히면 화면이 20초 뒤 "결과를 받지 못했어요" 로 떨어지고,
   그때 보호자는 무엇이 저장됐는지 모른다 (use-run-stream.ts 의 unconfirmed).

HTTP 없이 채널과 태스크만 본다.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest

from app.agents import entrypoint
from app.agents.common.suggestion import SuggestionDraft
from app.agents.entrypoint import (
    Done,
    EmptyResult,
    EventDrafts,
    Failed,
    MemoryNote,
    Partial,
    PendingMemoryContext,
    PendingReply,
    RetryContext,
    Step,
    Unwritten,
)
from app.agents.memory.schemas.task import WorkType
from app.api import agent_ports, idempotency
from app.api.runs import pending_reply, registry, runner, suggestion_retry
from app.domains.safety.agent_reader import DbSafetyReader

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
    # 안 넘기면 성장 추이가 "측정 0건" 으로 읽힌다
    from app.domains.child.agent_reader import DbGrowthLogReader

    assert isinstance(seen["growth_log"], DbGrowthLogReader)
    assert names(channel) == ["step", "done"]
    assert channel.closed


async def test_agent_job_좌표와_날씨_어댑터를_진입점에_그대로_넘긴다(monkeypatch):
    """창구가 위치 동의를 확인한 좌표와 서버 시작 때 만든 날씨 어댑터 (#380)."""
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    location, weather = entrypoint.CoarseLocation(lat=35.18, lon=129.08), object()

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text=RAW_TEXT,
        location=location,
        activity_weather=weather,
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert seen["location"] is location
    assert seen["activity_weather"] is weather


async def test_agent_job_안전_어댑터를_Food_Activity_Growth_에_넘긴다(monkeypatch):
    """#460 — 넘기지 않으면 Food · Activity 와 Growth 교육이 blocked.safety 로 닫힌다. 같은
    어댑터 하나를 셋이 다른 메서드로 읽는다."""
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert isinstance(seen["food_safety"], DbSafetyReader)
    assert seen["activity_safety"] is seen["food_safety"]
    assert seen["growth_safety"] is seen["food_safety"]


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


async def test_실패_맥락과_0개_기록은_보관함으로만_간다(monkeypatch):
    """실패 맥락에는 요청 원문 조각이 있다. 화면 · 로그로 보내지 않고 05 보관함에만 둔다 (#249)."""
    suggestion_retry.clear()
    sent_at = datetime(2026, 1, 1, tzinfo=UTC)

    async def fake_handle_input(**kwargs):
        emit = kwargs["emit"]
        run_id = kwargs["run_id"]
        emit(RetryContext(run_id, "food", "meal_recommendation", ("저녁 추천해줘",), sent_at))
        emit(EmptyResult(run_id, "activity", None, "조건에 맞는 추천이 없어요."))
        emit(Done(run_id, 0))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert names(channel) == ["done"]  # 화면에는 아무것도 가지 않는다
    claimed = suggestion_retry.claim(
        run_id=channel.run_id, parent_id=PARENT, child_id=CHILD, agent="food"
    )
    assert claimed == suggestion_retry.RetryTask("meal_recommendation", ("저녁 추천해줘",), sent_at)
    assert (
        suggestion_retry.empty_message(
            run_id=channel.run_id, parent_id=PARENT, child_id=CHILD, agent="activity"
        )
        == "조건에 맞는 추천이 없어요."
    )
    suggestion_retry.clear()


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


@pytest.mark.parametrize("granted", [True, False])
async def test_아이_건강정보_동의를_진입점에_값으로_넘긴다(monkeypatch, granted):
    """#372 PR 2 — 창구가 입력 한 번에 읽은 `child_health` 동의가 생일과 같이 Agent 까지 간다."""
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text=RAW_TEXT,
        birth_date=date(2025, 11, 1),
        child_health_granted=granted,
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert seen["child_health_granted"] is granted


async def test_동의_값을_안_받은_job_은_동의_없음으로_바꿔_넘기지_않는다(monkeypatch):
    """None 은 "값이 없다" 다. False 로 바뀌면 Agent 가 건강정보를 안 읽는 쪽으로 읽는다."""
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert seen["child_health_granted"] is None


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


async def test_commit_전_timeout은_failed로_끝나고_키가_풀린다(monkeypatch):
    """commit 전에 timeout이 걸리면 저장된 것이 없으니 failed + 키 풀림이 맞다."""

    async def fake_handle_input(**kwargs):
        await asyncio.sleep(999)  # commit 전에 timeout

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.1)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-timeout-pre"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=2)

    assert channel.ended_with == "failed"
    assert idempotency.recall(**scope) is None  # 키 풀림
    assert channel.closed


async def test_이어받기_timeout이_commit_뒤에_걸려도_done_이고_맥락_복구_안_함(monkeypatch):
    """이어받기 run에서 commit 후 timeout → done + 맥락 복구 안 함."""
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        kwargs["store"].wrote = True
        await kwargs["commit"]()
        await asyncio.sleep(999)

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.1)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        continuation=_PENDING,
        reply_to="r-prev",
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=2)

    assert channel.ended_with == "done"
    assert pending_reply.consume(run_id="r-prev", parent_id=PARENT, child_id=CHILD) is None
    pending_reply.clear()


async def test_이어받기_timeout이_commit_전에_걸리면_failed_이고_맥락_복구(monkeypatch):
    """이어받기 run에서 commit 전 timeout → failed + 맥락 복구."""
    pending_reply.clear()

    async def fake_handle_input(**kwargs):
        await asyncio.sleep(999)  # commit 전에 timeout

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.1)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text="3일 전부터",
        continuation=_PENDING,
        reply_to="r-prev",
    )
    await asyncio.wait_for(runner.start(channel, job, raw_text="3일 전부터"), timeout=2)

    assert channel.ended_with == "failed"
    assert pending_reply.consume(run_id="r-prev", parent_id=PARENT, child_id=CHILD) == _PENDING
    pending_reply.clear()


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


async def test_임베딩_키가_있으면_Curator_트리거를_만들어_진입점에_넘긴다(monkeypatch):
    """DB 세션을 여는 곳을 러너 한 곳에 모은다 (#257).

    store · commit 과 같이 Curator 트리거도 러너가 만든다.
    """
    from app.domains.memory.curator import trigger as trigger_module

    embedder, judge = object(), object()
    started: list[tuple] = []
    monkeypatch.setattr(entrypoint, "curator_deps", lambda: (embedder, judge))
    monkeypatch.setattr(
        trigger_module, "trigger_curator_background", lambda *args: started.append(args)
    )

    async def fake_handle_input(**kwargs):
        kwargs["curator_trigger"](CHILD, date(2026, 10, 14))  # pipeline 이 commit 직후 부르는 자리
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert started == [(CHILD, date(2026, 10, 14), embedder, judge)]


async def test_judge_키만_없어도_Curator_트리거는_넘긴다(monkeypatch):
    """judge 가 없으면 Curator 는 임베딩 · 보류까지만 한다. 트리거를 빼면 임베딩도 안 쌓인다."""
    from app.domains.memory.curator import trigger as trigger_module

    embedder = object()
    started: list[tuple] = []
    monkeypatch.setattr(entrypoint, "curator_deps", lambda: (embedder, None))
    monkeypatch.setattr(
        trigger_module, "trigger_curator_background", lambda *args: started.append(args)
    )

    async def fake_handle_input(**kwargs):
        kwargs["curator_trigger"](CHILD, date(2026, 10, 14))
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert started == [(CHILD, date(2026, 10, 14), embedder, None)]
    assert names(channel) == ["done"]


async def test_임베딩_키가_없으면_Curator_트리거를_넘기지_않는다(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(entrypoint, "curator_deps", lambda: (None, None))

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert seen["curator_trigger"] is None


async def test_run_세션은_deps_의_백그라운드_세션으로_열고_commit_도_그_세션이다(monkeypatch):
    """infra 세션을 import 하는 곳은 deps/db.py 하나다 (apps/api/CLAUDE.md). 러너도 그 문을 쓴다."""
    opened: list = []

    class FakeSession:
        def __init__(self) -> None:
            self.commits = 0

        async def __aenter__(self) -> "FakeSession":
            opened.append(self)
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            self.commits += 1

    monkeypatch.setattr(runner, "background_session", FakeSession)

    async def fake_handle_input(**kwargs):
        await kwargs["commit"]()
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert [session.commits for session in opened] == [1]


async def test_도메인_Agent_기억_포트는_열_때마다_run_세션과_다른_세션을_쓴다(monkeypatch):
    """run 세션을 Memory 와 도메인 task 가 같이 타면 AsyncSession 동시 사용 에러가 난다 (#257).

    도메인 단계는 Memory commit 뒤에 돌아서, 따로 연 세션에서도 방금 저장한 관찰이 보인다.
    """
    opened: list = []

    class FakeSession:
        async def __aenter__(self) -> "FakeSession":
            opened.append(self)
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(runner, "background_session", FakeSession)
    monkeypatch.setattr(agent_ports, "background_session", FakeSession)
    stores: list = []

    async def fake_handle_input(**kwargs):
        for _ in range(2):
            async with kwargs["domain_store"]() as store:
                stores.append(store)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    run_session, *domain_sessions = opened
    assert len(domain_sessions) == 2
    assert run_session not in domain_sessions
    assert domain_sessions[0] is not domain_sessions[1]
    assert [store._session for store in stores] == domain_sessions


_DRAFT = SuggestionDraft(agent="food", kind="general", content="계란말이", reason="또래 기준")


def _batch(run_id: str) -> entrypoint.SuggestionBatch:
    return entrypoint.SuggestionBatch(
        run_id=run_id, agent="food", task_type="meal_recommendation", drafts=(_DRAFT,)
    )


def _sessions(monkeypatch: pytest.MonkeyPatch) -> list:
    """러너가 여는 세션을 가짜로 바꾸고, 열린 순서대로 남긴다."""
    opened: list = []

    class FakeSession:
        def __init__(self) -> None:
            self.commits = 0

        async def __aenter__(self) -> "FakeSession":
            opened.append(self)
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            self.commits += 1

    monkeypatch.setattr(runner, "background_session", FakeSession)
    monkeypatch.setattr(agent_ports, "background_session", FakeSession)
    return opened


def _repository_save(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[dict]:
    """BE 저장 함수를 가짜로. 러너는 job 안에서 이 이름을 읽는다.

    DbMemoryStore 와 같은 늦은 import 라서 모듈 속성을 바꾸면 먹는다.
    """
    from app.domains.suggestion import repository

    calls: list[dict] = []

    async def save_agent_suggestions(session, **kwargs):
        calls.append(kwargs)
        if fail:
            raise RuntimeError("저장 실패")
        return [uuid.UUID(int=7 + index) for index in range(len(kwargs["payloads"]))]

    monkeypatch.setattr(repository, "save_agent_suggestions", save_agent_suggestions)
    return calls


async def test_추천은_묶음마다_새_세션에서_저장하고_commit_한다(monkeypatch):
    """트랜잭션 회의 표 — Agent 결과 하나마다 별도 트랜잭션. run 세션을 쓰지 않는다."""
    opened = _sessions(monkeypatch)
    calls = _repository_save(monkeypatch)
    returned: list = []

    async def fake_handle_input(**kwargs):
        for _ in range(2):
            returned.append(await kwargs["save_suggestions"](_batch(kwargs["run_id"])))
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    run_session, *save_sessions = opened
    assert [session.commits for session in save_sessions] == [1, 1]
    assert run_session not in save_sessions and run_session.commits == 0
    assert returned == [(str(uuid.UUID(int=7)),)] * 2  # 화면 · 05 가 쓰는 문자열 id
    assert (calls[0]["child_id"], calls[0]["run_id"]) == (CHILD, channel.run_id)
    assert calls[0]["payloads"] == [_DRAFT.to_payload()]
    assert channel.suggestions_saved is True


async def test_기록_건수_함수를_진입점에_넘기고_짧은_세션에서_센다(monkeypatch):
    """일반 추천 블록의 "쌓인 기록 N건" (#227). 05 와 같은 표로 센다(`agent_ports.RECORD_KINDS`)."""
    opened = _sessions(monkeypatch)
    from app.domains.suggestion import repository

    asked: list = []

    async def count_child_records(session, *, child_id, kinds):
        asked.append((child_id, tuple(kinds)))
        return 4

    monkeypatch.setattr(repository, "count_child_records", count_child_records)
    counted: list = []

    async def fake_handle_input(**kwargs):
        counted.append(await kwargs["count_records"]("activity"))
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert counted == [4]
    assert asked == [(CHILD, ("observation_activity",))]
    assert len(opened) == 2  # run 세션 + 건수 세션 — run 세션을 같이 타지 않는다


async def test_Food_카탈로그_어댑터를_진입점에_넘긴다(monkeypatch):
    """#442 — Food 가 DB 의 레시피 · 메뉴 카탈로그 · 추천 이력을 읽는다.

    어댑터는 만들 때 세션을 열지 않는다.
    """
    opened = _sessions(monkeypatch)
    seen: dict = {}

    async def fake_handle_input(**kwargs):
        seen.update(kwargs)
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert type(seen["food_recipes"]).__name__ == "DbRecipeCatalog"
    assert type(seen["food_catalog"]).__name__ == "DbMenuCatalog"
    assert type(seen["food_suggestions"]).__name__ == "DbSuggestionHistory"
    assert len(opened) == 1  # run 세션 하나뿐 — 어댑터는 호출할 때마다 짧은 세션을 연다


async def test_추천_저장_뒤_예외가_나도_done_이고_키를_풀지_않는다(monkeypatch):
    """저장된 draft 가 있는 run 을 failed 로 닫으면 키가 풀려 다시 보낼 때 두 벌이 된다
    (멘토 #196 ①)."""
    _sessions(monkeypatch)
    _repository_save(monkeypatch)

    async def fake_handle_input(**kwargs):
        await kwargs["save_suggestions"](_batch(kwargs["run_id"]))
        raise RuntimeError("저장 뒤에 터졌다")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-saved"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert channel.ended_with == "done"
    assert idempotency.recall(**scope) == channel.run_id


async def test_추천_저장_뒤_시간_안전망에_걸려도_done_이다(monkeypatch):
    _sessions(monkeypatch)
    _repository_save(monkeypatch)

    async def fake_handle_input(**kwargs):
        await kwargs["save_suggestions"](_batch(kwargs["run_id"]))
        await asyncio.sleep(999)  # 안전망 유발

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    monkeypatch.setattr(runner, "RUN_DEADLINE_SECONDS", 0.1)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-saved-timeout"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=2)

    assert channel.ended_with == "done"
    assert idempotency.recall(**scope) == channel.run_id


async def test_추천_저장이_실패하면_확정된_저장으로_세지_않는다(monkeypatch):
    _sessions(monkeypatch)
    _repository_save(monkeypatch, fail=True)

    async def fake_handle_input(**kwargs):
        with pytest.raises(RuntimeError):
            await kwargs["save_suggestions"](_batch(kwargs["run_id"]))
        raise RuntimeError("그 뒤에 터졌다")

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    channel = registry.open_run(parent_id=PARENT)
    scope = {"parent_id": PARENT, "method": "POST", "path": "/inputs", "key": "k-save-failed"}
    idempotency.remember(**scope, replay=channel.run_id)

    job = runner.agent_job(child_id=CHILD, parent_id=PARENT, raw_text=RAW_TEXT)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=1)

    assert channel.suggestions_saved is False
    assert channel.ended_with == "failed"
    assert idempotency.recall(**scope) is None
