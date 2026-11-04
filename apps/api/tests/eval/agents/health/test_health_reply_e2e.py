"""Health 가 되묻고 보호자가 답하는 두 턴 이상의 흐름 — 실제 모델로 운영 입구를 돈다.

운영 입구는 러너가 부르는 `entrypoint.handle_input` 이다. 첫 턴은 Supervisor · Health 가
실모델이고, 이어받는 턴은 앞 턴의 `PendingReply.context` 를 `continuation` 으로 넘긴다. 러너가
`reply_to` 로 보관함에서 꺼내 주는 것과 같다. `test_health_run.py` 는 앞 턴의 `pending` 을 다음 턴
context 에 직접 넣어 흉내 냈지만, 여기서는 pipeline 이 맥락을 만들고 되돌려 준다.

첫 턴은 실모델 Supervisor 를 거친다 — 중단 문장("항생제 끊었어." · "그 약 끊어줘." · "복약
일정 삭제해줘.")이 Health `medication` 으로 가는지까지 이 흐름의 일부로 본다. 이어받는 턴은
Supervisor 를 부르지 않는다.

    unset PYTEST_ADDOPTS   # PowerShell: Remove-Item Env:PYTEST_ADDOPTS
    uv run pytest tests/eval/agents/health/test_health_reply_e2e.py -m live -s
    TEST_REPEAT=3 uv run pytest tests/eval/agents/health/test_health_reply_e2e.py -m live -s

보는 것은 코드로 판정되는 것뿐이다: 질문이 답을 받는 질문(`awaits_reply`)으로 나갔는가, 첫 턴의
쓰기가 0 인가, 이어받는 턴의 모델이 0회인가, 쓰기와 안내 문구가 기대대로인가, 보관한 맥락에
원문이 없는가. 입력은 `test_input_health.txt` 의 문장이고 전부 지어낸 것이다.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.agents import entrypoint
from app.agents.health.readouts import READOUTS
from app.agents.memory.schemas.task import PendingMemoryContext
from app.agents.pipeline import (
    AgentQuestion,
    AgentResult,
    MedicationDrafts,
    PendingReply,
    PipelineResult,
)
from app.agents.suggestion_save import InMemorySuggestions
from tests.eval.agents.health.cases import doc_inputs
from tests.eval.agents.health.world import CHILD, KST, WORLDS, World, build_world

RESULT_PATH = Path(
    os.getenv(
        "TEST_RESULT_PATH", str(Path(__file__).with_name("test_health_reply_e2e_results.jsonl"))
    )
)
TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))
PARENT = CHILD  # 지어낸 id — 말한 보호자. 판정은 쓰지 않는다
INPUTS = doc_inputs()


async def _no_commit() -> None:
    """InMemoryStore 라 확정할 것이 없다."""


@dataclass
class Turn:
    text: str
    events: list[Any]
    result: PipelineResult
    writes: int  # 이 턴이 끝난 뒤 복약 저장소에 쌓인 쓰기 수(중단)
    ms: int

    def of(self, kind: type) -> list[Any]:
        return [event for event in self.events if isinstance(event, kind)]

    @property
    def bodies(self) -> list[str]:
        return [r.body for card in self.of(AgentResult) for r in card.readouts]

    @property
    def context(self) -> PendingMemoryContext | None:
        replies = self.of(PendingReply)
        return replies[-1].context if replies else None

    @property
    def question(self) -> AgentQuestion | None:
        questions = self.of(AgentQuestion)
        return questions[0] if questions else None


Judge = Callable[[list[Turn], list[str]], None]


@dataclass(frozen=True)
class Case:
    case_id: str
    texts: tuple[str, ...]  # 첫 턴 입력, 이어서 보호자의 답들
    world: str
    expect: str
    judge: Judge


def _need(failures: list[str], ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def _asks(turn: Turn, kind: str, failures: list[str], label: str) -> None:
    """이 턴이 `kind` 질문을 답을 받는 질문으로 냈고 맥락에 원문이 없는가."""
    question, context = turn.question, turn.context
    _need(
        failures, question is not None and question.awaits_reply, f"{label}: awaits_reply 가 아니다"
    )
    asked = None if context is None else context.asked
    _need(failures, asked is not None and asked.kind == kind, f"{label}: 맥락이 {kind} 가 아니다")
    if context is not None:
        no_text = context.hint_text == "" and (
            context.resume is None or context.resume.request_texts == ()
        )
        _need(failures, no_text, f"{label}: 보관한 맥락에 원문이 있다")


def _reply_ok(turn: Turn, failures: list[str], label: str) -> None:
    _need(
        failures,
        turn.result.model_calls == 0,
        f"{label}: 모델을 {turn.result.model_calls}회 불렀다",
    )
    _need(failures, turn.result.failed is None, f"{label}: run 이 실패로 끝났다")


def j_stop(answer_yes: bool) -> Judge:
    def judge(turns: list[Turn], failures: list[str]) -> None:
        first, reply = turns
        _asks(first, "medication_stop", failures, "첫 턴")
        _need(failures, first.writes == 0, "첫 턴이 쓰기를 했다")
        _reply_ok(reply, failures, "답 턴")
        _need(failures, reply.writes == (1 if answer_yes else 0), f"답 턴 쓰기 {reply.writes}건")
        key = "med.stopped" if answer_yes else "med.stop_kept"
        _need(
            failures,
            any(
                body.startswith(READOUTS.render(key, title="항생제").body[:6])
                for body in reply.bodies
            ),
            f"답 턴에 {key} 안내가 없다",
        )
        _need(failures, reply.question is None, "답 턴이 또 물었다")

    return judge


def j_which(then: str) -> Judge:
    def judge(turns: list[Turn], failures: list[str]) -> None:
        first = turns[0]
        _asks(first, "medication_which", failures, "첫 턴")
        _need(failures, first.writes == 0, "첫 턴이 쓰기를 했다")
        for turn in turns[1:]:
            _reply_ok(turn, failures, "답 턴")
        if then == "unclear":
            reply = turns[1]
            _need(failures, reply.writes == 0, "못 가린 답이 썼다")
            _need(
                failures,
                reply.question is None and reply.context is None,
                "못 가린 답이 다시 물었다",
            )
            _need(
                failures,
                READOUTS.render("med.which_unclear").body in reply.bodies,
                "which_unclear 가 없다",
            )
            return
        _asks(turns[1], "medication_stop", failures, "대상 약 답 턴")
        _need(failures, turns[1].writes == 0, "대상 약 답 턴이 쓰기를 했다")
        _need(failures, turns[2].writes == 1, f"중단 확인 답 턴 쓰기 {turns[2].writes}건 (1건)")

    return judge


def j_timing(clear: bool) -> Judge:
    def judge(turns: list[Turn], failures: list[str]) -> None:
        first, reply = turns
        _asks(first, "dose_timing", failures, "첫 턴")
        _need(failures, first.writes == 0, "첫 턴이 쓰기를 했다")
        _reply_ok(reply, failures, "답 턴")
        _need(failures, reply.writes == 0, "초안 단계에서 저장했다")
        drafts = [draft for batch in reply.of(MedicationDrafts) for draft in batch.drafts]
        if clear:
            _need(failures, len(drafts) == 1, f"초안이 {len(drafts)}장이다 (1장)")
            if drafts:
                _need(
                    failures,
                    drafts[0].notice_times == ("08:30", "12:30", "19:30"),
                    f"알림 시각 {drafts[0].notice_times}",
                )
        else:
            _need(failures, not drafts, "못 가린 시점에 초안이 나갔다")
            _need(failures, reply.question is None, "못 가린 시점에 다시 물었다")
            _need(
                failures,
                READOUTS.render("med.timing_unclear").body in reply.bodies,
                "timing_unclear 가 없다",
            )

    return judge


T02, T13, T14, T15 = INPUTS["T02"], INPUTS["T13"], INPUTS["T14"], INPUTS["T15"]

CASES: tuple[Case, ...] = (
    Case(
        "T02",
        (T02, "식후요"),
        "C48",
        "시점 되묻기 → '식후요' → 초안 1장(08:30 · 12:30 · 19:30) · 저장 0",
        j_timing(True),
    ),
    Case(
        "T02-unclear",
        (T02, "몰라"),
        "C48",
        "시점 되묻기 → '몰라' → timing_unclear · 초안 0 · 다시 묻지 않음",
        j_timing(False),
    ),
    Case("T13", (T13, "응"), "C48", "중단 확인 → '응' → 멈춤 · 답 턴 모델 0회", j_stop(True)),
    Case(
        "T13-no",
        (T13, "아니 그냥 둬"),
        "C48",
        "중단 확인 → '아니' → 변경 0 · stop_kept",
        j_stop(False),
    ),
    Case("T15", (T15, "응"), "C48", "중단 확인 → '응' → 멈춤", j_stop(True)),
    Case(
        "T14-2med",
        (T14, "항생제", "응"),
        "C48-2med",
        "어느 약 → '항생제' → 중단 확인 → '응' → 그 약만 멈춤",
        j_which("stop"),
    ),
    Case(
        "T14-2med-unclear",
        (T14, "글쎄"),
        "C48-2med",
        "어느 약 → 못 가린 답 → which_unclear · 다시 묻지 않음",
        j_which("unclear"),
    ),
)


def test_케이스의_입력과_세계가_있다() -> None:
    """모델을 부르지 않는다 — 오타로 라이브가 엉뚱한 입력을 돌지 않게."""
    assert len({case.case_id for case in CASES}) == len(CASES)
    for case in CASES:
        assert all(case.texts) and case.world in WORLDS, case.case_id
        assert len(case.texts) >= 2, f"{case.case_id}: 답이 없는 케이스다"


def test_케이스가_문서_입력의_두_턴_케이스를_다룬다() -> None:
    assert {case.case_id.split("-")[0] for case in CASES} == {"T02", "T13", "T14", "T15"}


async def _turn(
    text: str,
    run_id: str,
    world: World,
    store: Any,
    continuation: PendingMemoryContext | None,
) -> Turn:
    events: list[Any] = []
    birth = await world.context.ports.profile.birth_date(child_id=CHILD)
    started = time.perf_counter()
    result = await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text=text,
        run_id=run_id,
        birth_date=birth,
        child_health_granted=world.spec.consent,
        emit=events.append,
        store=store,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        health_safety=world.context.ports.safety,
        health_medication=world.medication,
        health_medication_writer=world.medication,
        continuation=continuation,
    )
    ms = int((time.perf_counter() - started) * 1000)
    return Turn(text, events, result, len(world.medication.writes), ms)


@dataclass
class Observed:
    case: Case
    turns: list[Turn]
    failures: list[str] = field(default_factory=list)


async def _run_once(case: Case, attempt: int) -> Observed:
    from app.agents.memory.store import InMemoryStore

    now = datetime.now(KST)
    base = f"reply-{case.case_id}-{attempt}"
    world = build_world(case.world, now=now, run_id=base)
    store = InMemoryStore(now=now)
    turns: list[Turn] = []
    continuation: PendingMemoryContext | None = None
    for index, text in enumerate(case.texts):
        turn = await _turn(text, f"{base}-t{index}", world, store, continuation)
        turns.append(turn)
        continuation = turn.context
        if index < len(case.texts) - 1 and continuation is None:
            break  # 이어받을 맥락이 없다 — 판정이 이유를 적는다
    observed = Observed(case, turns)
    if len(turns) < len(case.texts):
        routed = [str(t.task_type) for t in turns[-1].result.routing.domain_tasks]
        observed.failures.append(
            f"{len(turns)}번째 턴이 맥락을 남기지 않아 답을 이어 가지 못했다 (Health task {routed})"
        )
    else:
        case.judge(turns, observed.failures)
    return observed


def _record(o: Observed, attempt: int) -> dict[str, Any]:
    return {
        "case": o.case.case_id,
        "attempt": attempt,
        "outcome": "FAIL" if o.failures else "PASS",
        "expect": o.case.expect,
        "turns": [
            {
                "model_calls": turn.result.model_calls,
                "ms": turn.ms,
                "writes": turn.writes,
                "asked": None if turn.question is None else turn.question.awaits_reply,
                "readouts": len(turn.bodies),
                "drafts": sum(len(batch.drafts) for batch in turn.of(MedicationDrafts)),
            }
            for turn in o.turns
        ],
        "failures": o.failures,
    }


@pytest.mark.live
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_Health_되묻기_두_턴_판정(case: Case, expect: Callable[..., None]) -> None:
    expect(" → ".join(case.texts) + "  " + case.expect)
    broken: list[str] = []
    for attempt in range(1, TEST_REPEAT + 1):
        observed = await _run_once(case, attempt)
        record = _record(observed, attempt)
        with RESULT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        calls = [turn["model_calls"] for turn in record["turns"]]
        print(f"\n[{case.case_id} #{attempt}] {record['outcome']} 턴별 모델 {calls}")
        for failure in observed.failures:
            print(f"    실패: {failure}")
        broken += [f"#{attempt} {item}" for item in observed.failures]
    assert not broken, f"{case.case_id}: " + " / ".join(broken)
