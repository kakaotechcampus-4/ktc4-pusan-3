"""테스트 실행 기록을 테스트 파일마다 `<파일이름>_result.txt` 로 남긴다.

한 테스트에 다섯 가지를 적는다 — [질문] · [기대] · agent call · 사용한 tool · [답변].
실패했으면 그 이유를 한 줄 더 적는다.

[기대] 는 "성공하면 어떤 결과여야 하는가"
테스트가 `expect` fixture 로 적어 두면 그걸 쓰고,
없으면 docstring 첫 줄, 그것도 없으면 테스트 이름을 문장으로 되돌린다

"""

import functools
import json
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.agents.food import agent as food_agent
from app.agents.food import registry as food_registry
from app.agents.memory import agent as memory_agent
from app.agents.memory import registry as memory_registry
from app.agents.supervisor import agent as supervisor_agent

_CLIP = 200  # 한 줄에 싣는 질문·답변 길이
_DOMAINS = ("food", "health", "education", "activity", "routine")

# 저장된 행은 실제 모델을 부르는 테스트(-m live)에만 적는다.
# 어떤 값이 어느 컬럼에 들어갔는지 보는 용도다.
# 가짜 LLM 테스트에서도 보고 싶으면 $env:RESULT_SAVED_ROWS="1"
_ALWAYS_ROWS = os.getenv("RESULT_SAVED_ROWS") == "1"


@dataclass
class AgentCall:
    """agent 를 한 번 부른 기록."""

    agent: str
    question: str
    model_calls: int = 0
    tools: list[str] = field(default_factory=list)
    answer: str = ""
    saved: dict[str, Any] | None = None  # 저장된 행. None 은 "안 떴다", {} 는 "저장 없음"
    failures: list[str] = field(default_factory=list)  # 실패한 tool 호출의 코드와 사유


@dataclass
class TestRecord:
    name: str
    outcome: str
    expected: list[str] = field(default_factory=list)
    calls: list[AgentCall] = field(default_factory=list)
    direct_tools: list[tuple[str, str]] = field(default_factory=list)
    reason: str = ""


_pending: list[AgentCall] = []
_direct: list[tuple[str, str]] = []  # agent 를 거치지 않고 테스트가 직접 부른 tool (이름, raw_text)
_expected: list[str] = []  # 이번 테스트가 expect 로 적어 둔 기대
_running: dict[str, bool] = {"active": False, "rows": False}
_records: dict[Path, list[TestRecord]] = {}


def _add(call: AgentCall) -> None:
    if _running["active"]:
        _pending.append(call)


@pytest.fixture
def expect() -> Callable[..., None]:
    """성공하면 어떤 결과여야 하는지 적어 둔다. 결과 파일의 [기대] 가 된다.

    케이스가 파라미터로 도는 테스트(라이브 eval)에서 쓴다 — 이름이 `[T13-env]` 뿐이라
    무엇을 기대했는지 결과만 보고는 알 수 없다.
    """

    def declare(*parts: str) -> None:
        _expected.extend(part for part in parts if part)

    return declare


_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")


def _readable(name: str) -> str:
    """pytest 는 파라미터 id 의 한글을 \\uXXXX 로 적는다. 사람이 읽게 되돌린다."""
    return _ESCAPED.sub(lambda match: chr(int(match.group(1), 16)), name)


def _clip(text: str, limit: int = _CLIP) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[:limit]}…"


def _tool_names(calls: list[Any]) -> list[str]:
    """실행한 tool. 실패한 호출에는 ✗ 를 붙이고 같은 tool 은 묶어서 센다."""
    names = [f"{call.name}{'' if call.success else ' ✗'}" for call in calls]
    return [name if count == 1 else f"{name} ×{count}" for name, count in Counter(names).items()]


def _tool_failures(calls: list[Any]) -> list[str]:
    """실패한 호출의 코드와 사유.

    ✗ 만 남기면 "왜 실패했는지" 를 다시 돌려 봐야만 알 수 있다 (라이브 T14).
    tool 이 주는 message 에는 값 원문이 없고 필드 이름과 사유만 있다.
    """
    failures = []
    for call in calls:
        if call.success:
            continue
        error = call.result.get("error") or {}
        failures.append(f"{call.name} ✗ {error.get('code')} — {error.get('message', '')}".strip())
    return failures


# ── agent 진입점 감싸기 ─────────────────────────────────────────
_memory_run = memory_agent.run
_supervisor_run = supervisor_agent.run
_food_run = food_agent.run


@functools.wraps(_memory_run)
async def _memory_wrapper(*args: Any, **kwargs: Any) -> Any:
    question = args[0] if args else kwargs.get("raw_text", "")
    context = args[1] if len(args) > 1 else kwargs.get("context")
    try:
        result = await _memory_run(*args, **kwargs)
    except Exception as exc:
        # 죽기 전에 저장된 게 있으면 그것도 남긴다 (부분 저장)
        _add(
            AgentCall(
                "memory",
                question,
                answer=f"(예외 {type(exc).__name__})",
                saved=await _rows(context),
            )
        )
        raise
    answer = result.final_message or f"(문장 없음 · ended_by={result.ended_by})"
    _add(
        AgentCall(
            "memory",
            question,
            result.steps,
            _tool_names(result.calls),
            answer,
            await _rows(context),
            _tool_failures(result.calls),
        )
    )
    return result


async def _rows(context: Any) -> dict[str, Any] | None:
    """저장된 행을 그대로 뜬다. 어떤 값이 어느 컬럼에 들어갔는지 눈으로 보려는 것이다."""
    if not _running["rows"]:
        return None
    store = getattr(context, "store", None)
    child_id = getattr(context, "child_id", None)
    if store is None or child_id is None:
        return None

    rows: dict[str, Any] = {}
    for domain in _DOMAINS:
        found = await store.query_observations(domain=domain, child_id=child_id)
        if found:
            rows[f"observation_{domain}"] = [
                {
                    "id": row.id,
                    "raw_text": row.raw_text,
                    "observed_on": row.observed_on,
                    **row.fields,
                }
                for row in found
            ]
    for event in await store.query_events(child_id=child_id):
        items = await store.list_event_items(event_id=event.id)
        rows.setdefault("event", []).append(
            {
                "id": event.id,
                "title": event.title,
                "starts_at": event.starts_at,
                "all_day": event.all_day,
                **event.fields,
                "items": [item.item_name for item in items],
            }
        )
    return rows


@functools.wraps(_supervisor_run)
async def _supervisor_wrapper(*args: Any, **kwargs: Any) -> Any:
    question = args[0] if args else kwargs.get("raw_text", "")
    try:
        result = await _supervisor_run(*args, **kwargs)
    except Exception as exc:
        _add(AgentCall("supervisor", question, answer=f"(예외 {type(exc).__name__})"))
        raise
    tools = ["route"] if result.model_calls else []
    _add(AgentCall("supervisor", question, result.model_calls, tools, _segments(result)))
    return result


@functools.wraps(_food_run)
async def _food_wrapper(*args: Any, **kwargs: Any) -> Any:
    task = args[0] if args else kwargs.get("task")
    question = " / ".join(getattr(task, "request_texts", ()) or ())
    try:
        result = await _food_run(*args, **kwargs)
    except Exception as exc:
        _add(AgentCall("food", question, answer=f"(예외 {type(exc).__name__})"))
        raise
    opened = " · ".join(result.tools) or "없음"
    answer = (
        f"{result.task_type}·{result.stage} → {result.status} "
        f"(열릴 tool {len(result.tools)}개: {opened})"
    )
    # mock 은 tool 을 실행하지 않는다. 그래서 "사용한 tool" 이 아니라 답변에 열릴 tool 을 적는다
    _add(AgentCall("food", question, result.model_calls, [], answer))
    return result


def _segments(result: Any) -> str:
    """Supervisor 의 답은 조각과 라벨이다."""
    if result.output is None:
        return f"(조각 없음 · {result.error} {result.detail or ''})".strip()
    parts = []
    for segment in result.output.segments:
        label = segment.work or segment.agent or segment.guard or ""
        if segment.food_task:
            label = f"{label}·{segment.food_task}"
        parts.append(
            f"{segment.kind}/{label} {segment.text!r}"
            if label
            else f"{segment.kind} {segment.text!r}"
        )
    return " | ".join(parts)


memory_agent.run = _memory_wrapper
supervisor_agent.run = _supervisor_wrapper
food_agent.run = _food_wrapper


# tool 을 직접 부르는 테스트(오류 주입 · registry 계열)도 "사용한 tool" 을 남긴다.
# agent 는 위에서 이미 import 를 마쳐 원래 함수를 들고 있으므로 두 번 세지 않는다
def _wrap_execute(module: Any) -> None:
    execute_tool = module.execute_tool

    @functools.wraps(execute_tool)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        name = args[0] if args else kwargs.get("name", "?")
        arguments = args[1] if len(args) > 1 else kwargs.get("arguments", {})
        result = await execute_tool(*args, **kwargs)
        code = (result.error or {}).get("code") if not result.success else None
        label = f"{name} ✗({code})" if code else str(name)
        _direct.append((label, str(arguments.get("raw_text", "") if arguments else "")))
        return result

    module.execute_tool = wrapper


_wrap_execute(memory_registry)
_wrap_execute(food_registry)


# ── pytest 훅 ───────────────────────────────────────────────────
def pytest_runtest_setup(item: pytest.Item) -> None:
    _pending.clear()
    _direct.clear()
    _expected.clear()
    _running["active"] = True
    _running["rows"] = _ALWAYS_ROWS or item.get_closest_marker("live") is not None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: Any) -> Any:
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _finish(item, report)


def _finish(item: pytest.Item, report: Any) -> None:
    labels = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}
    outcome = labels.get(report.outcome, report.outcome.upper())
    if getattr(report, "wasxfail", None) is not None:
        outcome = "XPASS" if report.outcome == "passed" else "XFAIL"
    record = TestRecord(
        name=_readable(item.name),
        outcome=outcome,
        expected=_expectation(item),
        calls=list(_pending),
        direct_tools=list(_direct),
        reason=_reason(report),
    )
    _records.setdefault(Path(str(item.path)), []).append(record)
    _pending.clear()
    _direct.clear()
    _expected.clear()
    _running["active"] = False


def _expectation(item: pytest.Item) -> list[str]:
    """성공하면 어떤 결과여야 하는가. expect → docstring 첫 줄 → 테스트 이름 순으로 찾는다."""
    if _expected:
        return [_clip(part, 300) for part in _expected]
    doc = getattr(getattr(item, "function", None), "__doc__", None) or ""
    if doc.strip():
        return [_clip(doc.strip().splitlines()[0])]
    name = getattr(item, "originalname", None) or item.name.split("[")[0]
    return [_readable(name).removeprefix("test_").replace("_", " ")]


def _reason(report: Any) -> str:
    """실패·건너뜀의 이유 한 줄. assert 상세(E 로 시작하는 줄)를 먼저 쓴다."""
    if report.passed and getattr(report, "wasxfail", None) is None:
        return ""
    if getattr(report, "wasxfail", None):
        return f"알고 있는 구멍 — {report.wasxfail}"
    if report.skipped and isinstance(report.longrepr, tuple):
        return str(report.longrepr[2])
    text = getattr(report, "longreprtext", "") or str(report.longrepr or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail = [line for line in lines if line.startswith("E ")] or lines[-1:]
    return _clip(" ".join(line.removeprefix("E").strip() for line in detail[:3]), 400)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    for path, records in _records.items():
        target = path.with_name(f"{path.stem}_result.txt")
        target.write_text(_format(path, records), encoding="utf-8")


# ── 파일 내용 ───────────────────────────────────────────────────
def _format(path: Path, records: list[TestRecord]) -> str:
    counts = Counter(record.outcome for record in records)
    summary = " · ".join(f"{outcome} {count}" for outcome, count in sorted(counts.items()))
    lines = [
        f"{path.name} 실행 결과",
        f"{datetime.now():%Y-%m-%d %H:%M:%S} · 테스트 {len(records)}개 · {summary}",
        "",
    ]
    for index, record in enumerate(records, start=1):
        lines.append(f"[{index}] {record.name}  {record.outcome}")
        lines.extend(_body(record))
        if record.reason:
            label = {"FAIL": "실패 이유", "SKIP": "건너뛴 이유"}.get(record.outcome, "표시 이유")
            lines.append(f"    {label}    {record.reason}")
        lines.append("")
    return "\n".join(lines)


def _body(record: TestRecord) -> list[str]:
    expected = _continued("[기대]     ", record.expected)
    if not record.calls and not record.direct_tools:
        return [*expected, "    (agent 도 tool 도 부르지 않는 테스트 — 스키마·규칙·저장소만 본다)"]

    questions = [call.question for call in record.calls]
    questions += [question for _, question in record.direct_tools]
    lines = [
        f"    [질문]      {_questions(questions)}",
        *expected,
        f"    agent call  {_agents(record.calls) or '(없음 — tool 을 직접 부른다)'}",
    ]
    tools = [(call.agent, " · ".join(call.tools)) for call in record.calls if call.tools]
    if record.direct_tools:
        names = list(dict.fromkeys(label for label, _ in record.direct_tools))
        tools.append(("직접 호출", " · ".join(names)))
    answers = [(call.agent, _clip(call.answer)) for call in record.calls if call.answer]
    lines.extend(_listed("사용한 tool ", tools))
    broken = [(call.agent, text) for call in record.calls for text in call.failures]
    if broken:  # 성공한 run에도 남긴다. 모델이 고쳐 부른 흔적이 여기서만 보인다
        lines.extend(_listed("실패한 호출 ", broken))
    lines.extend(_listed("[답변]     ", answers))
    lines.extend(_saved(record.calls))
    return lines


def _saved(calls: list[AgentCall]) -> list[str]:
    """저장된 행을 JSON 그대로. 마지막 상태 하나만 적는다."""
    snapshots = [call.saved for call in calls if call.saved is not None]
    if not snapshots:
        return []
    rows = snapshots[-1]
    if not rows:
        return ["    저장된 행   (없음)"]
    dump = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    return ["    저장된 행", *(f"      {line}" for line in dump.splitlines())]


def _questions(questions: list[str]) -> str:
    """한 발화가 agent·tool 마다 나뉘어 들어간다. 이미 적은 질문에 들어 있으면 빼고 적는다."""
    kept: list[str] = []
    for value in questions:
        question = " ".join(str(value).split())
        if question and not any(question in seen for seen in kept):
            kept.append(question)
    return _clip(" / ".join(kept)) if kept else "(없음)"


def _listed(title: str, items: list[tuple[str, str]]) -> list[str]:
    if not items:
        return [f"    {title} (없음)"]
    return _continued(title, [f"[{agent}] {text}" for agent, text in items])


def _continued(title: str, values: list[str]) -> list[str]:
    """첫 줄에만 제목을 붙이고 나머지 줄은 들여쓴다."""
    return [
        f"{'    ' + title + ' ' if index == 0 else ' ' * 16}{value}"
        for index, value in enumerate(values)
    ]


def _agents(calls: list[AgentCall]) -> str:
    parts = []
    for agent in ("supervisor", "memory", "food"):
        runs = [call for call in calls if call.agent == agent]
        if runs:
            models = sum(call.model_calls for call in runs)
            parts.append(f"{agent} {len(runs)}회 실행 · 모델 {models}회")
    return " | ".join(parts)
