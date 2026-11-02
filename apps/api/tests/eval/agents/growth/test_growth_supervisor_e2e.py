"""Supervisor 로 들어온 입력이 Growth 까지 가는지 — 실제 모델로 운영 입구를 돈다.

운영 입구는 러너가 부르는 `entrypoint.handle_input` 이다.

    unset PYTEST_ADDOPTS   # PowerShell: Remove-Item Env:PYTEST_ADDOPTS
    uv run pytest tests/eval/agents/growth/test_growth_supervisor_e2e.py -m live -s
    TEST_REPEAT=2 uv run pytest tests/eval/agents/growth/test_growth_supervisor_e2e.py -m live -s

`test_growth_live.py` 는 Growth 에 task 를 바로 넣고,
`supervisor/test.py` 는 Supervisor 까지만 돈다.
이 파일은 그 사이를 잇는다: 보호자가 쓴 한 줄 → Supervisor(실모델) → routing → Memory(실모델) →
Growth(실모델) → 추천 저장 → 화면에 나가는 `agent_result` 까지. DB 에 닿는 포트만 InMemory 로
바꾼다(저장소 · 안전 · 성장 기록).
모델 설정은 운영과 같다 — 역할별 `*_API_KEY` 가 비면 `MEMORY_*` 로 폴백한다.

보는 것은 코드로 판정되는 것뿐이다: Growth task 가 routing 에 잡혔는가 · 그 결과가 `failed` 가
아닌가 · "준비 중" 카드가 아닌가 · 닫힘 문구 글자 · 저장된 추천 수 · Memory 저장 · 평가 표현.
문장의 질은 판정하지 않는다. 입력은 `test_input_growth.txt` 와 `routing_cases.py` 의 GR 문장이고
전부 지어낸 것이다(실제 보호자 발화 · 아이 정보 없음).
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
from app.agents.growth.library import LibraryBooks
from app.agents.growth.readouts import (
    CLOSED_BOOK_API,
    CLOSED_INFANT_LEARNING,
    CLOSED_MEDICAL_ROUTINE,
    CLOSED_SYMPTOM_HABIT,
    READOUTS,
)
from app.agents.growth.store.inmemory import (
    InMemoryGrowthLog,
    InMemoryIssuedBooks,
    InMemorySafety,
)
from app.agents.growth.tools.books import MAX_BOOKS, SEARCH_POOL
from app.agents.pipeline import AgentResult, PipelineResult, Unavailable
from app.agents.suggestion_save import InMemorySuggestions
from app.core.config import settings
from app.integrations.data4library.client import Data4LibraryBooks
from app.rules.book_form import fits_age
from app.rules.evaluative import find_evaluative
from tests.eval.agents.growth import world as world_module
from tests.eval.agents.growth.world import (
    CHILD,
    KST,
    PARENT,
    WORLDS,
    birth_date_for,
    measurements_for,
    seed_store,
)
from tests.eval.agents.routing_cases import CASES_BY_ID
from tests.unit.agents.growth.support import doc_inputs

RESULT_PATH = Path(
    os.getenv("TEST_RESULT_PATH", str(Path(__file__).with_name("test_growth_e2e_results.jsonl")))
)
TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))
INPUTS = doc_inputs()
DOMAINS = ("food", "health", "education", "activity", "routine")
LEARN, ROUTINE, BOOK, REVIEW = (
    "learning_suggestion",
    "routine_coaching",
    "book_suggestion",
    "growth_review",
)


async def _no_commit() -> None:
    """InMemoryStore 라 확정할 것이 없다."""


@dataclass
class Observed:
    case: "Case"
    result: PipelineResult
    events: list[Any]
    saved: InMemorySuggestions
    stored_before: int
    stored_after: int
    elapsed_ms: int
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def growth_tasks(self) -> list[str | None]:
        return [t.task_type for t in self.result.routing.domain_tasks if t.agent == "growth"]

    @property
    def growth_events(self) -> list[AgentResult]:
        return [e for e in self.events if isinstance(e, AgentResult) and e.agent == "growth"]

    @property
    def growth_outcomes(self) -> list[Any]:
        return [d for d in self.result.domain if d.agent == "growth"]

    @property
    def growth_saved(self) -> int:
        return sum(len(b.drafts) for b in self.saved.batches if b.agent == "growth")

    @property
    def guidance_codes(self) -> list[str]:
        return [g.code for g in self.result.routing.guidance]


Judge = Callable[[Observed], None]


@dataclass(frozen=True)
class Case:
    case_id: str
    text: str
    world: str
    expect: str
    judge: Judge
    books: bool = False  # 실제 정보나루 어댑터를 붙인다 (DATA4LIBRARY_AUTH_KEY 가 있을 때)


# ── 판정 ─────────────────────────────────────────────────────────────────
def _common(o: Observed) -> None:
    """모든 케이스 — 운영에서 Growth 가 열려 있다는 것의 최소 조건."""
    result = o.result
    if result.failed is not None:
        o.failures.append(f"입력을 그대로 돌려줬다 ({result.failed.reason})")
    if result.supervisor.error:
        o.failures.append(f"Supervisor 출력이 버려졌다 ({result.supervisor.error})")
    if any(isinstance(e, Unavailable) and "growth" in e.agents for e in o.events):
        o.failures.append('Growth 가 "준비 중" 카드로 갔다')
    if result.partial is not None and "growth" in result.partial.failed:
        o.failures.append(f"Growth 가 결과를 못 냈다 ({result.partial.reason})")
    texts = [r.body for e in o.growth_events for r in e.readouts]
    texts += [d.content for b in o.saved.batches if b.agent == "growth" for d in b.drafts]
    texts += [d.reason for b in o.saved.batches if b.agent == "growth" for d in b.drafts]
    for text in texts:
        if found := find_evaluative(text):
            o.failures.append(f"평가 표현 {found}")
    if o.elapsed_ms > 20_000:
        o.notes.append(f"run {o.elapsed_ms}ms — 20초 예산을 넘겼다")


def _ran(o: Observed, *labels: str) -> AgentResult | None:
    """Growth 가 `labels` 중 하나로 돌았고 결과 이벤트가 하나 나갔는가."""
    if sorted(str(t) for t in o.growth_tasks) != sorted(labels):
        o.failures.append(f"routing 의 Growth task {o.growth_tasks} (기대 {list(labels)})")
        return None
    if len(o.growth_events) != 1:
        o.failures.append(f"Growth agent_result {len(o.growth_events)}개 (1개여야 한다)")
        return None
    return o.growth_events[0]


def j_learning(o: Observed) -> None:
    event = _ran(o, LEARN)
    if event is None:
        return
    if event.status != "completed" or not event.suggestion_ids:
        o.failures.append(
            f"교육 추천이 없다 (status={event.status}, {len(event.suggestion_ids)}개)"
        )
    o.notes.append(f"추천 {len(event.suggestion_ids)}개 · {event.kind}")


def j_routine(*, need_suggestion: bool) -> Judge:
    def judge(o: Observed) -> None:
        event = _ran(o, ROUTINE)
        if event is None:
            return
        if event.status == "failed":
            o.failures.append("루틴 결과를 못 냈다")
        asked = bool(event.question)
        if need_suggestion and not event.suggestion_ids:
            o.failures.append("루틴 추천이 없다")
        if not need_suggestion and not (event.suggestion_ids or asked or event.readouts):
            o.failures.append("추천 · 되묻기 · 안내 어느 것도 없다")
        o.notes.append(f"추천 {len(event.suggestion_ids)}개 · 되묻기 {asked}")

    return judge


def j_review(o: Observed) -> None:
    event = _ran(o, REVIEW)
    if event is None:
        return
    if [r.kind for r in event.readouts] != ["growth_delta"] or event.suggestion_ids:
        o.failures.append("growth_delta readout 하나만 나가야 한다")
    if any(out.model_calls for out in o.growth_outcomes):
        o.failures.append("성장 추이가 모델을 불렀다")


def j_closed(key: str, *label_options: str) -> Judge:
    """Growth 가 코드 문구로 닫았고 추천은 저장하지 않았다."""

    def judge(o: Observed) -> None:
        event = _ran(o, *label_options)
        if event is None:
            return
        if [r.body for r in event.readouts] != [READOUTS.get(key).template]:
            o.failures.append(f"{key} 문구가 글자 그대로 하나 나가지 않았다")
        if o.growth_saved:
            o.failures.append("닫힌 경로에서 Growth 추천이 저장됐다")
        if key in (CLOSED_MEDICAL_ROUTINE, CLOSED_SYMPTOM_HABIT, CLOSED_INFANT_LEARNING) and any(
            out.model_calls for out in o.growth_outcomes
        ):
            o.failures.append("코드로 닫힐 입력에 Growth 모델이 불렸다")

    return judge


def j_mixed_record_routine(o: Observed) -> None:
    """기록 + 루틴 요청 — 저장이 먼저고 Growth 가 같은 run 에서 돈다."""
    j_routine(need_suggestion=False)(o)
    if o.stored_after <= o.stored_before:
        o.failures.append("Memory 가 기록을 저장하지 않았다")


def j_mixed_record_review(o: Observed) -> None:
    j_review(o)
    if o.stored_after <= o.stored_before:
        o.failures.append("Memory 가 기록을 저장하지 않았다")


def j_book_and_routine(o: Observed) -> None:
    """도서 + 루틴 — 도서가 자리를 내줘서 루틴이 돈다."""
    j_routine(need_suggestion=False)(o)
    if "task_limit" not in o.guidance_codes:
        o.failures.append("뺀 도서 요청의 task_limit 안내가 없다")


def j_book_closed(o: Observed) -> None:
    """도서 포트(정보나루 키)가 없는 eval 세계라 닫힘이 맞다. 운영은 키가 있으면 열린다."""
    j_closed(CLOSED_BOOK_API, BOOK)(o)


def j_book_recommended(o: Observed) -> None:
    """정보나루에서 찾은 책이 저장된 추천으로 나갔다 — 1 ~ 3권, 근거는 그 책의 서지 행."""
    event = _ran(o, BOOK)
    if event is None:
        return
    if event.status != "completed" or not 1 <= len(event.suggestion_ids) <= MAX_BOOKS:
        o.failures.append(
            f"도서 추천이 없다 (status={event.status}, {len(event.suggestion_ids)}권, "
            f"{[r.body[:30] for r in event.readouts]})"
        )
        return
    for batch in o.saved.batches:
        if batch.agent != "growth":
            continue
        for draft in batch.drafts:
            books = [c for c in draft.citations if c.ref.kind == "book_catalog"]
            if draft.kind == "general" and len(books) != 1:
                o.failures.append("일반 도서 추천에 그 책의 서지 근거가 하나가 아니다")
            if draft.kind == "personalized" and (
                books or not any(c.ref.is_child_record for c in draft.citations)
            ):
                o.failures.append("개인화 도서 추천의 근거가 아이 기록만이 아니다")
            if draft.items != (draft.content,):
                o.failures.append("도서 추천의 준비물이 '제목(저자)' 하나가 아니다")
            o.notes.append(f"{draft.kind} · {draft.content}")


def j_no_growth(o: Observed) -> None:
    """'배웠대' 는 관찰이다 — Growth 를 부르지 않고 기록만 저장한다."""
    if o.growth_tasks or o.growth_events:
        o.failures.append(f"Growth 가 불렸다 {o.growth_tasks}")
    if o.stored_after <= o.stored_before:
        o.failures.append("Memory 가 기록을 저장하지 않았다")


def j_in_time(judge: Judge) -> Judge:
    """판정에 더해 run 이 20초 예산 안에 끝나야 한다 — 재호출이 마감에 걸려 후보를 잃지 않았는지."""

    def wrapped(o: Observed) -> None:
        judge(o)
        if o.elapsed_ms > 20_000:
            o.failures.append(f"run {o.elapsed_ms}ms — 20초를 넘겼다")

    return wrapped


def j_activity_and_learning(o: Observed) -> None:
    j_learning(o)
    if not any(t.agent == "activity" for t in o.result.routing.domain_tasks):
        o.failures.append("Activity task 가 routing 에 없다")


def _gr(case_id: str) -> str:
    return CASES_BY_ID[case_id].text


CASES: tuple[Case, ...] = (
    Case("T01/C48", INPUTS["T01"], "C48", "교육 → learning_suggestion · 추천 저장", j_learning),
    Case("T04/C30", INPUTS["T04"], "C30", "숫자 활동 → learning_suggestion", j_learning),
    Case(
        "T11/C48",
        INPUTS["T11"],
        "C48",
        "식사 자립 → routine_coaching",
        j_routine(need_suggestion=False),
    ),
    Case(
        "T13/C48-tooth",
        INPUTS["T13"],
        "C48-tooth",
        "양치 → routine 추천 저장",
        j_routine(need_suggestion=True),
    ),
    Case("T24/C48", INPUTS["T24"], "C48", "키 · 몸무게 추이 → growth_review · 모델 0", j_review),
    Case("T27/C48", INPUTS["T27"], "C48", "잘 크고 있어? → growth_review", j_review),
    Case(
        "T41/C48",
        INPUTS["T41"],
        "C48",
        "약 → closed.medical_routine",
        j_closed(CLOSED_MEDICAL_ROUTINE, ROUTINE),
    ),
    Case(
        "T42/C48",
        INPUTS["T42"],
        "C48",
        "깜빡임 → closed.symptom_habit",
        j_closed(CLOSED_SYMPTOM_HABIT, ROUTINE),
    ),
    Case(
        "GR17/C48",
        _gr("GR17"),
        "C48",
        "약 먹기 싫어해. 어떻게 먹이지? → 닫힘",
        j_closed(CLOSED_MEDICAL_ROUTINE, ROUTINE),
    ),
    Case("GR20/C48", _gr("GR20"), "C48", "기록 + 루틴 → 저장 먼저 · 루틴", j_mixed_record_routine),
    Case("GR22/C48", _gr("GR22"), "C48", "놀이 기록 + 성장 추이", j_mixed_record_review),
    Case("GR24/C48", _gr("GR24"), "C48", "도서 + 루틴 → 루틴만 · task_limit", j_book_and_routine),
    Case(
        "GR23/C48",
        _gr("GR23"),
        "C48",
        "나들이 + 한글 → Activity + Growth 교육 · 20초 안",
        j_in_time(j_activity_and_learning),
    ),
    Case(
        "T07/C48", INPUTS["T07"], "C48", "도서 → 포트 없는 eval 은 closed.book_api", j_book_closed
    ),
    Case("GR04/C48", _gr("GR04"), "C48", "'배웠대' → Growth 없음 · 기록만", j_no_growth),
    Case(
        "GR02/C48-books",
        _gr("GR02"),
        "C48",
        "관심 + 책 → 정보나루 책 추천 저장",
        j_book_recommended,
        books=True,
    ),
    Case(
        "GR09/C30-books",
        _gr("GR09"),
        "C30",
        "그림책 → 정보나루 책 추천 저장",
        j_book_recommended,
        books=True,
    ),
    Case(
        "T07/C48-books",
        INPUTS["T07"],
        "C48",
        "도서 입력 → 정보나루 책 추천 저장",
        j_book_recommended,
        books=True,
    ),
    Case(
        "BOOKANY/C48-books",
        "아이랑 같이 읽을 책 추천해줘",
        "C48",
        "주제 없는 요청 → 교육 · 놀이 · 생활 기록을 종합한 책",
        j_book_recommended,
        books=True,
    ),
    Case(
        "BOOKANY/C48-brush-books",
        "아이랑 같이 읽을 책 추천해줘",
        "C48-brush",
        "생활 기록(양치 힘들어함)만 있는 아이 → 그 행동을 다룬 책",
        j_book_recommended,
        books=True,
    ),
    Case(
        "BOOK8/C8-books",
        "아기랑 같이 볼 책 추천해줘",
        "C8",
        "8개월 → 보드북만 · 일반 추천",
        j_book_recommended,
        books=True,
    ),
    Case(
        "T01/C11",
        INPUTS["T01"],
        "C11",
        "11개월 교육 → closed.infant_learning",
        j_closed(CLOSED_INFANT_LEARNING, LEARN),
    ),
)


# ── 실행 ─────────────────────────────────────────────────────────────────
def test_케이스의_입력과_세계가_있다() -> None:
    """모델을 부르지 않는다 — 오타로 라이브가 엉뚱한 입력을 돌지 않게."""
    assert len({case.case_id for case in CASES}) == len(CASES)
    for case in CASES:
        assert case.text and case.world in WORLDS, case.case_id


async def _count_stored(store: Any) -> int:
    total = 0
    for domain in DOMAINS:
        total += len(await store.query_observations(domain=domain, child_id=CHILD))
    return total


async def _run_once(case: Case, attempt: int, monkeypatch: pytest.MonkeyPatch) -> Observed:
    # 운영 입구는 지금 시각을 쓴다 — 세계의 관찰 · 측정 날짜도 같은 날 기준으로 심는다
    now = datetime.now(KST)
    monkeypatch.setattr(world_module, "NOW", now)
    monkeypatch.setattr(world_module, "TODAY", now.date())
    spec = WORLDS[case.world]
    store = await seed_store(spec)
    birth = birth_date_for(spec.months, now.date())
    saved = InMemorySuggestions()
    events: list[Any] = []
    before = await _count_stored(store)
    book_ports: dict[str, Any] = {}
    if case.books:
        key = settings.DATA4LIBRARY_AUTH_KEY.strip()
        if not key:
            pytest.skip("DATA4LIBRARY_AUTH_KEY 가 없다 — apps/api/.env 에 넣고 돌린다")
        # 검색어 캐시는 쓰지 않는다 — 늘 실제 응답을 본다
        book_ports = {
            "growth_books": Data4LibraryBooks(auth_key=key, cache=None),
            "growth_issued_books": InMemoryIssuedBooks(),
        }
    started = time.perf_counter()
    result = await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text=case.text,
        run_id=f"e2e-{case.case_id}-{attempt}",
        birth_date=birth,
        child_health_granted=spec.consent,
        emit=events.append,
        store=store,
        commit=_no_commit,
        save_suggestions=saved,
        growth_safety=InMemorySafety(spec.safety, fail=spec.safety_fails),
        growth_log=InMemoryGrowthLog(measurements_for(spec.measurements, birth=birth)),
        **book_ports,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    after = await _count_stored(store)
    observed = Observed(case, result, events, saved, before, after, elapsed_ms)
    _common(observed)
    case.judge(observed)
    return observed


def _record(o: Observed, attempt: int) -> dict[str, Any]:
    result = o.result
    return {
        "case": o.case.case_id,
        "attempt": attempt,
        "outcome": "FAIL" if o.failures else "PASS",
        "request": o.case.text,
        "expect": o.case.expect,
        "ms": o.elapsed_ms,
        "model_calls": result.model_calls,
        "segments": [
            {
                "kind": str(s.kind),
                "agent": str(s.agent or ""),
                "growth_task": str(s.growth_task or ""),
            }
            for s in (result.supervisor.output.segments if result.supervisor.output else [])
        ],
        "growth_tasks": o.growth_tasks,
        "growth_events": [
            {
                "task_type": e.task_type,
                "status": e.status,
                "suggestions": len(e.suggestion_ids),
                "kind": e.kind,
                "readouts": [r.body[:40] for r in e.readouts],
                "asked": bool(e.question),
            }
            for e in o.growth_events
        ],
        "guidance": o.guidance_codes,
        "stored": o.stored_after - o.stored_before,
        "failures": o.failures,
        "notes": o.notes,
    }


@pytest.fixture(scope="module", autouse=True)
def _fresh_results() -> None:
    RESULT_PATH.unlink(missing_ok=True)


@pytest.mark.live
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
async def test_supervisor_에서_Growth_까지(
    case: Case, expect: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    expect(case.expect)
    broken: list[str] = []
    for attempt in range(1, TEST_REPEAT + 1):
        observed = await _run_once(case, attempt, monkeypatch)
        record = _record(observed, attempt)
        with RESULT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"\n[{case.case_id} #{attempt}] {record['outcome']} {observed.elapsed_ms}ms "
            f"growth={observed.growth_tasks} {record['growth_events']}"
        )
        for failure in observed.failures:
            print(f"    실패: {failure}")
        for note in observed.notes:
            print(f"    참고: {note}")
        broken += [f"#{attempt} {item}" for item in observed.failures]
    assert not broken, f"{case.case_id}: " + " / ".join(broken)


@pytest.mark.live
async def test_정보나루_검색이_월령에_맞는_책을_돌려준다() -> None:
    """모델 없이 정보나루만 부른다 — 응답이 오고, 월령 필터를 지난 책이 3권 이상 남는다."""
    key = settings.DATA4LIBRARY_AUTH_KEY.strip()
    if not key:
        pytest.skip("DATA4LIBRARY_AUTH_KEY 가 없다 — apps/api/.env 에 넣고 돌린다")
    rows = await LibraryBooks(Data4LibraryBooks(auth_key=key, cache=None)).search(
        keywords=("공룡",), limit=SEARCH_POOL
    )
    assert rows, "정보나루가 책을 돌려주지 않았다"
    assert all(row.isbn and row.title for row in rows)
    assert len(rows) <= SEARCH_POOL
    fit = [row for row in rows if fits_age(row.form, 30)]
    print(f"\n[정보나루] 공룡 {len(rows)}권 · 30개월에 맞는 책 {len(fit)}권")
    assert len(fit) >= MAX_BOOKS
