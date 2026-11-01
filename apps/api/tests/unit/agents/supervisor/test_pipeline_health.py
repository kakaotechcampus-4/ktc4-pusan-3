"""Health 를 pipeline 에 연결한 run 검증. 가짜 LLM 으로 돈다 (API 호출 0).

3200줄이 넘는 `test_pipeline.py` 에 더하지 않고 Health 연결은 여기에 모은다. Supervisor 응답은
`call("route", {"segments": [...]})` 이고 Health context 는 `support.context(...)` 다.
여기서 보는 것:
  연결    Supervisor 가 health 로 보낸 조각이 Health run() 까지 간다
  라벨    라벨이 비면 채우지 않고 unsupported 로 닫는다
  실패    Health 는 추천을 내지 않아 다시 시도 대상이 아니다
  순서    복약 task 는 같은 run 의 진료 준비보다 먼저 끝난다
"""

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.agents import pipeline
from app.agents.common.llm_client import LLMResponse
from app.agents.common.result import (
    DomainAgentResult,
    DomainPending,
    EventRequest,
    MedicationDraft,
)
from app.agents.health.envelope import result as health_result
from app.agents.health.readouts import EMERGENCY, READOUTS
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthObservation
from app.agents.memory.context import AgentContext
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore
from app.agents.pipeline import (
    AgentResult,
    EventDrafts,
    RetryContext,
    Saved,
    Unavailable,
    handle_input,
)
from app.agents.suggestion_save import InMemorySuggestions
from app.agents.supervisor.routing import Guidance
from tests.unit.agents.health.support import (
    CHILD,
    KST,
    NOW,
    TODAY,
    FakeLLM,
    birth_for,
    call,
    calls,
    context,
    observation,
)

RUN_ID = "run-1"
PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))


def _memory_context() -> AgentContext:
    return AgentContext(
        child_id=CHILD,
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


def _world(*titles: str) -> Any:
    """진행 중 코스가 `titles` 만큼 있는 Health context."""
    seeds = [
        course_with_doses(title, PATTERN, TODAY - timedelta(days=1), TODAY + timedelta(days=1))
        for title in titles
    ]
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    return context(medication=store)


def _segment(text: str, label: str | None) -> dict[str, Any]:
    segment: dict[str, Any] = {"text": text, "kind": "request", "agent": "health"}
    if label is not None:
        segment["health_task"] = label
    return segment


def _supervisor(*segments: dict[str, Any]) -> FakeLLM:
    return FakeLLM(call("route", {"segments": list(segments)}))


async def _no_commit() -> None:
    """InMemoryStore 라 확정할 것이 없다."""


async def _handle(
    raw_text: str,
    supervisor: FakeLLM,
    health: Any,
    events: list[Any],
    *,
    memory_llm: FakeLLM | None = None,
    continuation: PendingMemoryContext | None = None,
    contexts: dict[str, Any] | None = None,
) -> pipeline.PipelineResult:
    return await handle_input(
        raw_text,
        _memory_context(),
        contexts or {"health": health},
        run_id=RUN_ID,
        supervisor_client=supervisor,
        memory_client=memory_llm or FakeLLM(),
        emit=events.append,
        continuation=continuation,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
    )


def _of(events: list[Any], kind: type) -> list[Any]:
    return [event for event in events if isinstance(event, kind)]


@pytest.fixture
def events() -> list[Any]:
    return []


async def test_health_조각은_Health_run_으로_간다(events: list[Any]) -> None:
    raw = "아까 약 먹였어"
    result = await _handle(raw, _supervisor(_segment(raw, "medication")), _world("항생제"), events)

    assert [e.agent for e in _of(events, AgentResult)] == ["health"]
    assert result.domain[0].readouts[0].kind == "notice"  # dose.check_on_screen
    assert result.failed is None


async def test_라벨_없는_health_조각은_unsupported_모델_0회(events: list[Any]) -> None:
    raw = "약"
    result = await _handle(raw, _supervisor(_segment(raw, None)), _world("항생제"), events)

    assert result.domain[0].status == "unsupported" and result.domain[0].model_calls == 0
    assert result.domain[0].task_type is None


async def test_health_가_실패해도_다시_시도_대상이_아니다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(task: Any, context: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Health 가 터졌다")

    monkeypatch.setitem(pipeline._RUNNERS, "health", broken)
    raw = "아까 약 먹였어"

    result = await _handle(raw, _supervisor(_segment(raw, "medication")), _world("항생제"), events)

    partial = result.partial
    assert partial is not None
    assert partial.failed == ("health",) and partial.retryable == ()
    assert not _of(events, RetryContext)


async def test_복약_task_가_같은_run_의_진료_준비보다_먼저_끝난다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    async def recording(task: Any, context: Any, **kwargs: Any) -> Any:
        order.append(f"{task.task_type}:start")
        # 복약 task 가 더 늦게 끝나도 진료 준비는 기다려야 한다
        await asyncio.sleep(0.05 if task.task_type == "medication" else 0)
        order.append(f"{task.task_type}:end")
        return health_result(HealthTaskType(task.task_type), "completed")

    monkeypatch.setitem(pipeline._RUNNERS, "health", recording)
    # 진료 준비를 먼저 말해도 복약이 먼저 돈다
    raw = "내일 소아과 가는데 정리해줘. 아까 약 먹였어"
    supervisor = _supervisor(
        _segment("내일 소아과 가는데 정리해줘", "visit_summary"),
        _segment("아까 약 먹였어", "medication"),
    )

    await _handle(raw, supervisor, _world("항생제"), events)

    assert order.index("medication:end") < order.index("visit_summary:start")


def _fake_health(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    """Health runner 를 고정 결과를 내는 가짜로 바꾼다."""

    async def fixed(task: Any, context: Any, **kwargs: Any) -> Any:
        return outcome

    monkeypatch.setitem(pipeline._RUNNERS, "health", fixed)


def _order(events: list[Any]) -> list[str]:
    return [type(event).__name__ for event in events]


DRAFT = MedicationDraft(
    draft_id="d1",
    op="create",
    source="utterance",
    schedule={"title": "항생제"},
    doses=({"scheduled_time": "08:30"},),
    notice_times=("08:30",),
)
PENDING = DomainPending(
    agent="health", kind="medication_which", question="어느 약인가요?", payload={}
)


async def test_복약_초안과_되묻기가_AgentResult_까지_실린다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = health_result(
        HealthTaskType.MEDICATION,
        "completed",
        medication_drafts=(DRAFT,),
        question=PENDING.question,
        pending=PENDING,
    )
    _fake_health(monkeypatch, outcome)
    raw = "항생제 하루 세 번 먹여야 해"

    await _handle(raw, _supervisor(_segment(raw, "medication")), _world(), events)

    [agent_result] = _of(events, AgentResult)
    assert agent_result.medication_drafts == (DRAFT,) and agent_result.pending == PENDING
    assert agent_result.question == PENDING.question


async def test_검진_알림은_일정_초안_한_장이고_저장하지_않는다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = EventRequest(
        title="영유아 건강검진 4차",
        starts_at=datetime(2026, 10, 7, tzinfo=KST),
        category="health",
        all_day=True,
    )
    outcome = health_result(HealthTaskType.SCHEDULE_CHECK, "completed", event_requests=(request,))
    _fake_health(monkeypatch, outcome)
    raw = "검진 언제 받아야 돼?"

    result = await _handle(raw, _supervisor(_segment(raw, "schedule_check")), _world(), events)

    [drafts] = _of(events, EventDrafts)
    assert [d.title for d in drafts.drafts] == ["영유아 건강검진 4차"]
    assert drafts.drafts[0].draft_id
    assert result.committed is False
    assert _order(events).index("AgentResult") < _order(events).index("EventDrafts")


# ── hook ───────────────────────────────────────────────────────
_COUGH = {"raw_text": "오늘 기침을 했어", "observed_on": "오늘", "symptom": ["기침"]}
_APPLE = {"raw_text": "오늘 사과 먹었어", "observed_on": "오늘", "subject": "사과"}


def _coughing() -> Any:
    """7일 안 사흘 기침 기록이 있는 Health context — 반복 hook 이 걸린다."""
    memory = InMemoryHealthMemory(
        [observation(TODAY - timedelta(days=offset)) for offset in (0, 1, 2)]
    )
    return context(memory=memory)


def _observe(text: str) -> FakeLLM:
    return _supervisor({"text": text, "kind": "record", "work": "observe"})


def _hook_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """pipeline 이 Health hook 을 부른 횟수를 세는 가짜로 바꾼다."""
    seen: list[Any] = []

    async def counting(context: Any) -> Any:
        seen.append(context)
        return health_result(None, "completed")

    monkeypatch.setattr(pipeline, "run_health_hooks", counting)
    return seen


async def test_건강_기록을_저장하면_hook_이_돈다(events: list[Any]) -> None:
    raw = "오늘 기침을 했어"
    memory_llm = FakeLLM(call("create_observation_health", _COUGH))

    await _handle(raw, _observe(raw), _coughing(), events, memory_llm=memory_llm)

    [hooked] = [e for e in _of(events, AgentResult) if e.task_type is None]
    assert hooked.agent == "health" and hooked.readouts[0].kind == "symptom_repeat"
    assert _order(events).index("Saved") < _order(events).index("AgentResult")


async def test_저장이_안_된_run_에서는_hook_을_부르지_않는다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _hook_calls(monkeypatch)
    raw = "오늘 기침을 했어"

    # Memory 가 쓰기 없이 말만 하고 끝난다
    await _handle(raw, _observe(raw), _coughing(), events)

    assert not _of(events, Saved) and seen == []


async def test_health_가_아닌_관찰만_저장하면_hook_을_부르지_않는다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _hook_calls(monkeypatch)
    raw = "오늘 사과 먹었어"
    memory_llm = FakeLLM(call("create_observation_food", _APPLE))

    await _handle(raw, _observe(raw), _coughing(), events, memory_llm=memory_llm)

    assert _of(events, Saved) and seen == []


async def test_동의가_없으면_hook_결과가_없다(events: list[Any]) -> None:
    raw = "오늘 기침을 했어"
    memory = InMemoryHealthMemory([observation(TODAY - timedelta(days=d)) for d in (0, 1, 2)])
    memory_llm = FakeLLM(call("create_observation_health", _COUGH))

    await _handle(
        raw, _observe(raw), context(consent=False, memory=memory), events, memory_llm=memory_llm
    )

    assert _of(events, Saved)
    assert not [e for e in _of(events, AgentResult) if e.agent == "health"]


async def test_hook_이_터져도_run_은_이어진다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(context: Any) -> Any:
        raise LookupError("생일을 읽지 못했다")

    monkeypatch.setattr(pipeline, "run_health_hooks", broken)
    raw = "오늘 기침을 했어"
    memory_llm = FakeLLM(call("create_observation_health", _COUGH))

    with caplog.at_level("ERROR"):
        result = await _handle(raw, _observe(raw), _coughing(), events, memory_llm=memory_llm)

    # 관찰은 이미 commit 됐다. hook 이 터졌다고 run 을 실패로 끝내면 화면이 "저장 못 함" 으로 보인다
    assert result.failed is None and _of(events, Saved)
    assert "LookupError" in caplog.text
    assert "생일" not in caplog.text


async def test_이어받은_답이_건강_기록을_저장해도_hook_이_돈다(events: list[Any]) -> None:
    final = LLMResponse(
        message=SimpleNamespace(
            content=json.dumps(
                {"pending_hint": None, "leftover": False, "text": "기록했어요.", "kind": "message"}
            ),
            tool_calls=None,
        ),
        usage={},
        latency_ms=1,
    )
    memory_llm = FakeLLM(call("create_observation_health", _COUGH), final)
    pending = PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)

    await _handle(
        "오늘부터요",
        FakeLLM(),
        _coughing(),
        events,
        memory_llm=memory_llm,
        continuation=pending,
    )

    [hooked] = [e for e in _of(events, AgentResult) if e.task_type is None]
    assert hooked.readouts[0].kind == "symptom_repeat"


# ── 응급 안내는 run 에 한 번 ─────────────────────────────────────
def _fever_reading() -> HealthObservation:
    """한 시간 전에 잰 38.3도. 2개월 아이면 응급 hook 이 걸린다."""
    at = NOW - timedelta(hours=1)
    return HealthObservation(
        id=UUID(int=5001),
        observed_from=at.date(),
        observed_to=at.date(),
        observed_time=at,
        symptoms=(),
        temperature=Decimal("38.3"),
        measured_at=at,
    )


def test_응급_readout_kind_는_pipeline_과_같다() -> None:
    assert READOUTS.render(EMERGENCY).kind == pipeline.EMERGENCY_KIND


async def test_hook_과_진료_준비가_같이_걸려도_응급_안내는_한_번(events: list[Any]) -> None:
    raw = "열이 38.3도야. 내일 소아과 가는데 정리해줘"
    supervisor = _supervisor(
        {"text": "열이 38.3도야", "kind": "record", "work": "observe"},
        _segment("내일 소아과 가는데 정리해줘", "visit_summary"),
    )
    fever = {
        "raw_text": "열이 38.3도야",
        "observed_on": "오늘",
        "symptom": ["발열"],
        "temperature": "38.3",
    }
    health = context(birth_for(2), memory=InMemoryHealthMemory([_fever_reading()]))

    result = await _handle(
        raw,
        supervisor,
        health,
        events,
        memory_llm=FakeLLM(call("create_observation_health", fever)),
    )

    kinds = [r.kind for e in _of(events, AgentResult) for r in e.readouts]
    assert kinds.count("emergency") == 1
    # 화면 이벤트만 거른다. 결과 객체는 Health 가 낸 그대로다
    assert any(r.kind == "emergency" for o in result.domain for r in o.readouts)


# ── 등록된 약과 겹칠 때 ──────────────────────────────────────────
def _given(medicine: str, raw: str) -> dict[str, Any]:
    return {"raw_text": raw, "observed_on": "오늘", "medication_given": medicine}


def _medication_results(events: list[Any]) -> list[Any]:
    return [e for e in _of(events, AgentResult) if e.task_type == "medication"]


async def test_등록_안_된_약을_먹였다면_Memory_가_기록하고_Health_는_건너뛴다(
    events: list[Any],
) -> None:
    raw = "해열제 먹였어"
    memory_llm = FakeLLM(call("create_observation_health", _given("해열제", raw)))

    result = await _handle(
        raw,
        _supervisor(_segment(raw, "medication")),
        _world("항생제"),
        events,
        memory_llm=memory_llm,
    )

    assert _of(events, Saved) and not _medication_results(events)
    assert result.routing.domain_tasks == ()


async def test_등록된_약을_먹였다면_체크_안내만_내고_먹인_약_행은_없다(events: list[Any]) -> None:
    raw = "해열 시럽 먹였어"
    # Memory 가 쓰려다 코드에 거절된다
    memory_llm = FakeLLM(call("create_observation_health", _given("해열 시럽", raw)))

    await _handle(
        raw,
        _supervisor(_segment(raw, "medication")),
        _world("해열 시럽"),
        events,
        memory_llm=memory_llm,
    )

    assert not _of(events, Saved)
    [checked] = _medication_results(events)
    assert checked.readouts[0].kind == "notice"


async def test_순수_복약_요청도_Memory_를_거른다(events: list[Any]) -> None:
    raw = "해열제 먹였어"

    result = await _handle(raw, _supervisor(_segment(raw, "medication")), _world("항생제"), events)

    assert result.routing.memory_task is not None
    assert result.routing.memory_task.hints[0].work == "observe"


async def test_기록으로만_나뉜_먹였어도_등록된_약이면_체크_안내로(events: list[Any]) -> None:
    raw = "해열 시럽 먹였어"
    supervisor = _observe(raw)
    memory_llm = FakeLLM(call("create_observation_health", _given("해열 시럽", raw)))

    await _handle(raw, supervisor, _world("해열 시럽"), events, memory_llm=memory_llm)

    # 막은 것이지 놓친 것이 아니라 다시 나누지 않는다
    assert not _of(events, Saved) and len(supervisor.seen) == 1
    [checked] = _medication_results(events)
    assert checked.readouts[0].kind == "notice"


class _FakeContext:
    def for_task(self) -> "_FakeContext":
        return self


async def test_Agent_상한이_차_있으면_복약_조각을_더하지_않는다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fixed(agent: str) -> Any:
        async def run(task: Any, context: Any, **kwargs: Any) -> Any:
            return DomainAgentResult(agent=agent, task_type=task.task_type, status="completed")

        return run

    monkeypatch.setitem(pipeline._RUNNERS, "food", fixed("food"))
    monkeypatch.setitem(pipeline._RUNNERS, "activity", fixed("activity"))
    raw = "간식 추천해줘. 놀이 추천해줘. 해열 시럽 먹였어"
    supervisor = _supervisor(
        {
            "text": "간식 추천해줘",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
        {"text": "놀이 추천해줘", "kind": "request", "agent": "activity"},
        {"text": "해열 시럽 먹였어", "kind": "record", "work": "observe"},
    )
    contexts = {
        "health": _world("해열 시럽"),
        "food": _FakeContext(),
        "activity": _FakeContext(),
    }

    result = await _handle(raw, supervisor, None, events, contexts=contexts)

    assert {t.agent for t in result.routing.domain_tasks} == {"food", "activity"}


# ── 응급 사전검사 — 안내 먼저, 도메인 Agent 는 건너뛴다, 기록은 평소처럼 ─────────
def _emergency_guidance(events: list[Any]) -> list[Any]:
    return [e for e in events if isinstance(e, Guidance) and e.code == "emergency"]


async def test_응급_신호면_Supervisor_보다_먼저_안내가_나간다(events: list[Any]) -> None:
    raw = "숨을 잘 못 쉬어."

    await _handle(raw, _observe(raw), _world(), events)

    assert isinstance(events[0], Guidance) and events[0].code == "emergency"
    assert _order(events).index("Guidance") < _order(events).index("Step")


async def test_응급_신호면_도메인_Agent_를_부르지_않는다(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def counting(task: Any, context: Any, **kwargs: Any) -> Any:
        calls.append(task.task_type)
        return health_result(HealthTaskType.VISIT_SUMMARY, "completed")

    monkeypatch.setitem(pipeline._RUNNERS, "health", counting)
    raw = "두 달 된 아기가 열이 39도인데 정리해줘"
    supervisor = _supervisor(
        {"text": "두 달 된 아기가 열이 39도인데", "kind": "record", "work": "observe"},
        _segment("정리해줘", "visit_summary"),
    )

    result = await _handle(raw, supervisor, _world(), events)

    assert calls == [] and not _of(events, Unavailable) and result.partial is None
    assert result.routing.domain_tasks == ()


async def test_응급_신호여도_기록은_평소처럼_남는다(events: list[Any]) -> None:
    raw = "열이 39도인데 숨을 잘 못 쉬어"
    fever = {"raw_text": raw, "observed_on": "오늘", "symptom": ["발열"], "temperature": "39"}

    result = await _handle(
        raw,
        _observe(raw),
        _world(),
        events,
        memory_llm=FakeLLM(call("create_observation_health", fever)),
    )

    assert _of(events, Saved) and result.emergency


async def test_응급_안내는_run_에_한_번(events: list[Any]) -> None:
    raw = "두 달 된 아기가 열이 38도야"
    fever = {"raw_text": raw, "observed_on": "오늘", "symptom": ["발열"], "temperature": "38"}
    health = context(birth_for(2), memory=InMemoryHealthMemory([_fever_reading()]))

    await _handle(
        raw,
        _observe(raw),
        health,
        events,
        memory_llm=FakeLLM(call("create_observation_health", fever)),
    )

    # 사전검사 안내 한 번, 그 뒤 hook 이 같은 응급을 만들어도 화면에는 나가지 않는다
    count = len(_emergency_guidance(events)) + sum(
        r.kind == "emergency" for e in _of(events, AgentResult) for r in e.readouts
    )
    assert count == 1


async def test_응급_안내만_냈어도_실패가_아니다(events: list[Any]) -> None:
    raw = "숨을 잘 못 쉬어"

    result = await _handle(raw, _supervisor(_segment(raw, "visit_summary")), _world(), events)

    assert result.failed is None and result.emergency


async def test_이어받은_답에_응급_신호가_있어도_먼저_낸다(events: list[Any]) -> None:
    pending = PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE)

    result = await _handle(
        "어제부터인데 지금 숨을 잘 못 쉬어요", FakeLLM(), _world(), events, continuation=pending
    )

    assert isinstance(events[0], Guidance) and events[0].code == "emergency"
    assert result.emergency and result.failed is None


async def test_응급이면_다시_나누지_않는다(events: list[Any]) -> None:
    raw = "숨을 잘 못 쉬어"
    supervisor = _observe(raw)

    # Memory 가 힌트를 적지 않아도 Supervisor 를 다시 부르지 않는다. 응급 안내가 이미 나갔다
    await _handle(raw, supervisor, _world(), events)

    assert len(supervisor.seen) == 1


async def test_오탐_문장은_평소대로(events: list[Any]) -> None:
    raw = "어제 경련 얘기를 들었어"

    result = await _handle(raw, _observe(raw), _world(), events)

    assert not _emergency_guidance(events) and not result.emergency


async def test_일부만_말한_등록_약도_체크_안내로_간다(events: list[Any]) -> None:
    raw = "시럽 먹였어"
    # Memory 게이트는 "시럽" 을 "해열 시럽" 으로 보고 거절한다
    memory_llm = FakeLLM(call("create_observation_health", _given("시럽", raw)))

    await _handle(raw, _observe(raw), _world("해열 시럽"), events, memory_llm=memory_llm)

    assert not _of(events, Saved)
    [checked] = _medication_results(events)
    assert checked.readouts[0].kind == "notice"


async def test_이름_없는_약도_등록_약이_있으면_체크_안내로_간다(events: list[Any]) -> None:
    raw = "열나서 약 먹였어"
    memory_llm = FakeLLM(call("create_observation_health", _given("약", raw)))

    await _handle(raw, _observe(raw), _world("해열 시럽"), events, memory_llm=memory_llm)

    assert not _of(events, Saved)
    [checked] = _medication_results(events)
    assert checked.readouts[0].kind == "notice"


async def test_등록_안_된_약과_등록_약을_같이_말하면_둘_다_남는다(events: list[Any]) -> None:
    raw = "해열제랑 해열 시럽 먹였어"
    memory_llm = FakeLLM(
        calls(
            ("create_observation_health", _given("해열제", raw)),
            ("create_observation_health", _given("해열 시럽", raw)),
        )
    )

    await _handle(raw, _observe(raw), _world("해열 시럽"), events, memory_llm=memory_llm)

    # 해열제는 먹인 약 행으로 남고, 시럽은 체크 안내가 맡는다
    assert _of(events, Saved)
    [checked] = _medication_results(events)
    assert checked.readouts[0].kind == "notice"


async def test_사전검사가_이미_낸_응급만_있는_hook_은_빈_결과를_내보내지_않는다(
    events: list[Any],
) -> None:
    raw = "두 달 된 아기가 열이 38도야"
    fever = {"raw_text": raw, "observed_on": "오늘", "symptom": ["발열"], "temperature": "38"}
    health = context(birth_for(2), memory=InMemoryHealthMemory([_fever_reading()]))

    await _handle(
        raw,
        _observe(raw),
        health,
        events,
        memory_llm=FakeLLM(call("create_observation_health", fever)),
    )

    # hook 의 응급 readout 은 사전검사 안내와 겹쳐 걸러진다. 남은 것이 없으면 이벤트도 없다
    empty = [e for e in _of(events, AgentResult) if e.agent == "health" and not e.readouts]
    assert empty == []


async def test_라벨_없는_health_조각은_조용히_사라지지_않고_준비_중으로_알린다(
    events: list[Any],
) -> None:
    raw = "애가 요즘 자꾸 아파서 걱정이야 병원 가야 할까?"

    result = await _handle(raw, _supervisor(_segment(raw, None)), _world("항생제"), events)

    assert [e.agents for e in _of(events, Unavailable)] == [("health",)]
    assert result.failed is None


async def test_먹였더니_도_등록된_약이면_체크_안내로_간다(events: list[Any]) -> None:
    raw = "해열 시럽 먹였더니 열이 내렸어"
    memory_llm = FakeLLM(call("create_observation_health", _given("해열 시럽", raw)))

    await _handle(raw, _observe(raw), _world("해열 시럽"), events, memory_llm=memory_llm)

    assert not _of(events, Saved)
    [checked] = _medication_results(events)
    assert checked.readouts[0].kind == "notice"


async def test_응급이면_개수_제한_안내도_내지_않는다(events: list[Any]) -> None:
    raw = "숨을 잘 못 쉬어. 간식 추천해줘. 놀이 추천해줘. 책 추천해줘"
    supervisor = _supervisor(
        {"text": "숨을 잘 못 쉬어.", "kind": "record", "work": "observe"},
        {
            "text": "간식 추천해줘.",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
        {"text": "놀이 추천해줘.", "kind": "request", "agent": "activity"},
        {
            "text": "책 추천해줘",
            "kind": "request",
            "agent": "growth",
            "growth_task": "book_suggestion",
        },
    )

    await _handle(raw, supervisor, _world(), events)

    # 준비하는 것이 없는데 "한 번에 2가지까지 준비할 수 있어요" 가 119 안내 밑에 나가면 안 된다
    assert [g.code for g in events if isinstance(g, Guidance)] == ["emergency"]
