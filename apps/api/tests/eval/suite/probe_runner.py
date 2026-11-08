"""P · 단발 프로브 러너 (docs/eval/eval-v1.md §3-4).

운영 입구 `entrypoint.handle_input` 을 실제 모델로 부른다. 여러 턴 케이스는 앞 턴의 Memory
되묻기(`memory.pending`)를 다음 턴의 continuation 으로 넘긴다. 판정은 마지막 턴만 한다.
"""

import traceback
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from app.agents import entrypoint
from app.agents.pipeline import AgentQuestion, AgentResult, MemoryNote
from app.agents.suggestion_save import InMemorySuggestions, SuggestionBatch
from app.agents.supervisor.routing import Guidance
from app.core import llm_usage
from tests.eval.suite.schema import ProbeCase
from tests.eval.suite.trace import CitationView, SuggestionView, ToolCallView, Trace
from tests.eval.suite.world import (
    CHILD,
    KST,
    PARENT,
    build_probe_world,
    observation_rows,
    stored_event_count,
)


async def _no_commit() -> None:
    """InMemoryStore 라 확정할 것이 없다."""


def memory_tools(memory: Any) -> tuple[ToolCallView, ...] | None:
    """Memory 가 부른 tool — 이름과 성공 여부만. Memory 가 돌지 않았으면 None."""
    if memory is None:
        return None
    return tuple(ToolCallView(call.name, call.success) for call in memory.calls)


def read_run(
    trace: Trace,
    *,
    routing: Any,
    drafts: int,
    model_calls: int,
    latency_ms: int,
    partial: bool,
    failed: str | None,
    events: Sequence[object],
    batches: Sequence[SuggestionBatch],
) -> None:
    trace.intent = routing.intent_type
    trace.route = tuple(task.agent for task in routing.domain_tasks)
    trace.degraded = routing.degraded
    trace.event_drafts = drafts
    trace.model_calls = model_calls
    trace.latency_ms = latency_ms
    trace.partial = partial
    trace.failed = failed
    trace.event_names = tuple(type(event).__name__ for event in events)
    trace.guidance = tuple(e.code for e in events if isinstance(e, Guidance))
    questions: list[str] = []
    agent_texts: list[str] = []
    health_texts: list[str] = []
    for event in events:
        if isinstance(event, AgentResult):
            trace.bundle_kinds[event.agent] = event.kind
            trace.record_counts[event.agent] = event.record_count
            texts = [t for r in event.readouts for t in (r.title, r.body) if t]
            (health_texts if event.agent == "health" else agent_texts).extend(texts)
        elif isinstance(event, AgentQuestion):
            questions.append(event.text)
        elif isinstance(event, MemoryNote):
            trace.memory_text = event.text
            if event.kind == "question":
                questions.append(event.text)
    trace.questions = tuple(questions)
    trace.agent_texts = tuple(agent_texts)
    trace.health_texts = tuple(health_texts)
    trace.suggestions = tuple(
        SuggestionView(
            agent=batch.agent,
            kind=draft.kind,
            content=draft.content,
            reason=draft.reason,
            items=tuple(draft.items),
            citations=tuple(
                CitationView(
                    kind=c.ref.kind,
                    id=str(c.ref.id),
                    note=c.note,
                    child_record=c.ref.is_child_record,
                )
                for c in draft.citations
            ),
        )
        for batch in batches
        for draft in batch.drafts
    )


def usage_rows(prefix: str) -> list[dict[str, Any]]:
    """이 케이스 · 반복의 호출만 꺼낸다 (같은 계약 llm_usage — #710 과 세는 방식이 같다)."""
    return [
        {
            "run_id": r.run_id,
            "role": r.role,
            "model": r.model,
            "prompt_tokens": r.prompt_tokens,
            "cached_prompt_tokens": r.cached_prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "reasoning_tokens": r.reasoning_tokens,
            "latency_ms": r.latency_ms,
        }
        for r in llm_usage.drain(limit=10_000)
        if r.run_id.startswith(prefix)
    ]


async def run_probe(case: ProbeCase, *, attempt: int, logs: Callable[[], Sequence[str]]) -> Trace:
    now = datetime.now(KST)
    world = await build_probe_world(case.world, now=now)
    trace = Trace(
        case_id=case.id,
        attempt=attempt,
        about=case.turns[-1].about,
        canaries=tuple(turn.say for turn in case.turns),
        stale_ids=world.stale_ids,
    )
    prefix = f"eval-{case.id}-{attempt}-"
    try:
        continuation = None
        for index, turn in enumerate(case.turns):
            if turn.reply and continuation is None:
                trace.error = "앞 턴이 되묻지 않아 답 턴을 돌릴 수 없다"
                break
            world.store.seen.clear()  # 읽은 기록은 이 턴 것만 센다 (I3 = 이 run 의 근거)
            world.affinity.seen.clear()
            before = await observation_rows(world.store.inner)
            events_before = await stored_event_count(world.store.inner)
            events: list[object] = []
            saved = InMemorySuggestions()
            run_id = f"{prefix}{index}"
            with llm_usage.attribute(parent_id=PARENT, run_id=run_id, source="input"):
                result = await entrypoint.handle_input(
                    child_id=CHILD,
                    parent_id=PARENT,
                    raw_text=turn.say,
                    run_id=run_id,
                    emit=events.append,
                    commit=_no_commit,
                    save_suggestions=saved,
                    continuation=continuation if turn.reply else None,
                    **world.ports,
                )
            continuation = result.memory.pending if result.memory is not None else None
            if index < len(case.turns) - 1:
                if result.failed is not None:
                    trace.error = f"앞 턴 실패: {result.failed.reason}"
                    break
                continue
            after = await observation_rows(world.store.inner)
            trace.run_id = run_id
            trace.saved = tuple(row for oid, row in after.items() if oid not in before)
            trace.stored_events = await stored_event_count(world.store.inner) - events_before
            trace.searched = world.searched
            read_run(
                trace,
                routing=result.routing,
                drafts=len(result.memory.drafts) if result.memory is not None else 0,
                model_calls=result.model_calls,
                latency_ms=result.latency_ms,
                partial=result.partial is not None,
                failed=result.failed.reason if result.failed is not None else None,
                events=events,
                batches=saved.batches,
            )
            trace.memory_tools = memory_tools(result.memory)
            if not result.committed:
                # 확정되지 않은 run 의 쓰기는 운영에서 롤백된다(pipeline 의 commit 결과가
                # False 면 저장 안 된 것) — InMemory 에 남은 행을 저장으로 세지 않는다
                trace.saved = ()
    except Exception as exc:  # run 하나의 예외로 케이스 전체를 멈추지 않는다
        trace.error = f"{type(exc).__name__}: {exc}"
        trace.error_detail = traceback.format_exc()
    trace.usage = tuple(usage_rows(prefix))
    trace.logs = tuple(logs())
    return trace
