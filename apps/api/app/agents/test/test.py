"""Memory Agent 라이브 eval. 실제 모델을 부르고 run()을 돌린다.
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest apps/api/app/agents/test/test.py -m live -s

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
MEMORY_API_KEY / MEMORY_BASE_URL / MEMORY_MODEL 은 apps/api/.env 에서 읽는다.

프로토타입과 달라진 점 — 이 파일은 프롬프트도 tool 스펙도 루프도 갖고 있지 않다.
run() 을 부르고 저장된 결과를 본다. 사본을 측정하면 구현을 고쳐도 점수가 안 변한다.

인자를 직접 검사하지 않는 이유: 날짜·시각은 모델이 표현만 주고 코드가 확정한다.
"모델이 2026-09-11 을 보냈는가" 가 아니라 "운동회가 9/11 로 저장됐는가"를 확인해야 한다.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.datetime_rules import DateRange
from app.agents.common.llm_client import LLMClient
from app.agents.memory.agent import MemoryAgentResult, run
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import EventItemRow, EventRow, ObservationRow, ReminderRow

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)  # 수요일. 상대 날짜 해석을 고정한다
CHILD = UUID("00000000-0000-7000-8000-000000000001")
WRITER = UUID("00000000-0000-7000-8000-0000000000ff")

DOMAINS = ("food", "health", "education", "activity")
MUTATING = "쓰기"  # forbidden_tools 에 넣으면 create/update/delete 전부를 금지한다

RESULT_PATH = Path(
    os.getenv("EVAL_RESULT_PATH", str(Path(__file__).with_name("eval_results.jsonl")))
)
INPUT_PATH = Path(os.getenv("EVAL_INPUT_PATH", str(Path(__file__).with_name("test_input.txt"))))
EVAL_MODELS = [x.strip() for x in os.getenv("EVAL_MODELS", "").split(",") if x.strip()]

_CLARIFY = ("무엇을", "어떤", "알려주", "말씀해", "확인이 필요", "골라", "선택", "할까요", "인가요")
_OUT_OF_SCOPE = ("범위", "추천", "진단", "직접 입력", "할 수 없", "하지 않", "드릴 수 없", "어려워")


# ── 결과 스냅샷 ─────────────────────────────────────────────────
@dataclass
class Snapshot:
    """run() 이 끝난 뒤의 호출 흔적 + 저장된 상태."""

    result: MemoryAgentResult
    observations: dict[str, list[ObservationRow]]
    events: list[tuple[EventRow, list[EventItemRow], list[ReminderRow]]]

    def count(self, tool: str) -> int:
        return sum(1 for call in self.result.calls if call.name == tool)

    def rows(self, domain: str) -> list[ObservationRow]:
        return self.observations[domain]

    def fields(self, domain: str) -> list[dict[str, Any]]:
        return [row.fields for row in self.rows(domain)]

    def observed_on(self, domain: str) -> list[str]:
        return [row.observed_on.isoformat() for row in self.rows(domain)]

    def starts_at(self) -> str:
        first = self.events[0][0] if self.events else None
        return first.starts_at.astimezone(KST).isoformat() if first else ""

    @property
    def item_names(self) -> list[str]:
        return [item.item_name for _, items, _ in self.events for item in items]

    @property
    def remind_at(self) -> list[str]:
        return [
            reminder.remind_at.astimezone(KST).isoformat()
            for _, _, reminders in self.events
            for reminder in reminders
        ]

    @property
    def wrote_anything(self) -> bool:
        return any(self.observations[domain] for domain in DOMAINS) or bool(self.events)

    def said(self, needles: tuple[str, ...]) -> bool:
        text = self.result.final_message or ""
        return any(needle in text for needle in needles)


Check = tuple[str, Callable[[Snapshot], bool]]
Seed = Callable[[AgentContext], Awaitable[None]]


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    text: str
    seed: Seed | None = None
    required_tools: dict[str, int] = field(default_factory=dict)
    forbidden_tools: set[str] = field(default_factory=set)
    expect_parse: bool | None = None  # True=불러야 · False=부르면 안 됨 · None=따지지 않음
    expect_clarification: bool = False
    expect_out_of_scope: bool = False
    checks: tuple[Check, ...] = ()


# ── seed — 조회·수정·삭제 케이스가 기댈 기존 기록 ───────────────
async def _seed_block_play(context: AgentContext) -> None:
    await context.store.create_observation(
        domain="activity",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="어제 블록놀이를 20분 했어",
        observed_on=date(2026, 9, 8),
        observed_range=DateRange(start=date(2026, 9, 8), end=date(2026, 9, 9)),
        fields={"activity": "블록놀이", "subject": "블록놀이", "duration_min": 20},
    )


async def _seed_apple(context: AgentContext) -> None:
    await context.store.create_observation(
        domain="food",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="어제 사과를 먹었어",
        observed_on=date(2026, 9, 8),
        observed_range=DateRange(start=date(2026, 9, 8), end=date(2026, 9, 9)),
        fields={"subject": "사과", "action": "먹었다"},
    )


async def _seed_sports_day(context: AgentContext) -> None:
    await context.store.create_event(
        child_id=CHILD,
        title="운동회",
        starts_at=datetime(2026, 9, 11, tzinfo=KST),
        ends_at=None,
        all_day=True,
        fields={"status": "draft", "created_by": "caregiver", "category": "institution"},
    )


def _no_guess(domain: str, key: str) -> Check:
    return (
        f"{domain}.{key} 를 지어내지 않았다",
        lambda s: all(f.get(key) in (None, "") for f in s.fields(domain)),
    )


# ── 케이스 ──────────────────────────────────────────────────────
CASES: list[EvalCase] = [
    EvalCase(
        "T01",
        "오늘 민준이가 아침에 사과를 반 개 먹었어.",
        required_tools={"create_observation_food": 1},
        forbidden_tools={"create_observation_activity", "create_event"},
        expect_parse=False,
        checks=(
            (
                "사과가 저장됐다",
                lambda s: any("사과" in str(f.get("subject")) for f in s.fields("food")),
            ),
            (
                "양이 보존됐다",
                lambda s: any("반" in str(f.get("amount")) for f in s.fields("food")),
            ),
            ("오늘로 기록됐다", lambda s: s.observed_on("food") == ["2026-09-09"]),
        ),
    ),
    EvalCase(
        "T02",
        "내일 아침은 바나나를 먹일 예정이야.",
        forbidden_tools={MUTATING},
        checks=(("아무것도 저장하지 않았다", lambda s: not s.wrote_anything),),
    ),
    EvalCase(
        "T03",
        "오늘 유치원에서 레고로 성을 만들면서 친구랑 계속 놀았대.",
        required_tools={"create_observation_activity": 1},
        forbidden_tools={"create_observation_education", "create_observation_food"},
        checks=(
            _no_guess("activity", "duration_min"),
            (
                "전해 들은 말로 기록됐다",
                lambda s: all(
                    f.get("confidence_source") == "parent_hearsay" for f in s.fields("activity")
                ),
            ),
        ),
    ),
    EvalCase(
        "T04",
        "오늘 집에서 한글 자모 활동지를 20분 했어. 끝까지 집중해서 풀었어.",
        required_tools={"create_observation_education": 1},
        forbidden_tools={"create_observation_activity"},
        checks=(
            (
                "topic 이 한글 자모다",
                lambda s: any(
                    "한글" in str(f.get("topic")) or "자모" in str(f.get("topic"))
                    for f in s.fields("education")
                ),
            ),
            (
                "20분이 저장됐다",
                lambda s: any(f.get("duration_min") == 20 for f in s.fields("education")),
            ),
        ),
    ),
    EvalCase(
        "T05",
        "선생님 말로는 오늘 낮에 콧물이 좀 났는데 열은 없었대.",
        required_tools={"create_observation_health": 1},
        forbidden_tools={"create_observation_activity", "create_observation_food"},
        checks=(
            (
                "콧물이 저장됐다",
                lambda s: any(
                    any("콧물" in str(x) for x in f.get("symptom", [])) for f in s.fields("health")
                ),
            ),
            (
                "없다고 한 열은 빠졌다",
                lambda s: (
                    not any(
                        any("열" in str(x) for x in f.get("symptom", []))
                        for f in s.fields("health")
                    )
                ),
            ),
            (
                "전해 들은 말로 기록됐다",
                lambda s: all(
                    f.get("confidence_source") == "parent_hearsay" for f in s.fields("health")
                ),
            ),
            _no_guess("health", "observed_time"),
        ),
    ),
    EvalCase(
        "T06",
        "오늘 저녁으로 닭갈비를 먹였어. 오늘 유치원에서는 하루 종일 쌓기놀이를 했대. "
        "모레 운동회가 있고 체육복을 가져가야 해. 운동회 알림은 내일 오전 8시에 만들어줘. "
        "그리고 내일 일정도 알려줘.",
        required_tools={
            "create_observation_food": 1,
            "create_observation_activity": 1,
            "create_event": 1,
            "create_event_item": 1,
            "create_reminder": 1,
            "query_event": 1,
        },
        expect_parse=True,
        checks=(
            ("운동회가 9/11 로 저장됐다", lambda s: s.starts_at().startswith("2026-09-11")),
            ("체육복이 준비물에 있다", lambda s: any("체육복" in name for name in s.item_names)),
            (
                "알림이 9/10 08:00 이다",
                lambda s: any(x.startswith("2026-09-10T08:00") for x in s.remind_at),
            ),
            (
                "하루 종일이 1440 이다",
                lambda s: any(f.get("duration_min") == 1440 for f in s.fields("activity")),
            ),
        ),
    ),
    EvalCase(
        "T07",
        "내일 무슨 일정 있어?",
        required_tools={"query_event": 1},
        forbidden_tools={MUTATING},
    ),
    EvalCase(
        "T08",
        "어제 블록놀이 했다고 기록한 거 보여줘.",
        seed=_seed_block_play,
        required_tools={"query_observation_activity": 1},
        forbidden_tools={MUTATING},
    ),
    EvalCase(
        "T09",
        "어제 블록놀이를 20분 했다고 기록했는데 40분으로 바꿔줘.",
        seed=_seed_block_play,
        required_tools={"query_observation_activity": 1, "update_observation_activity": 1},
        forbidden_tools={"create_observation_activity", "delete_observation_activity"},
        checks=(
            (
                "40분으로 바뀌었다",
                lambda s: [f.get("duration_min") for f in s.fields("activity")] == [40],
            ),
        ),
    ),
    EvalCase(
        "T10",
        "어제 사과 먹었다고 저장한 기록 지워줘.",
        seed=_seed_apple,
        required_tools={"query_observation_food": 1, "delete_observation_food": 1},
        forbidden_tools={"create_observation_food"},
        checks=(("음식 기록이 지워졌다", lambda s: s.rows("food") == []),),
    ),
    EvalCase(
        "T11",
        "모레 운동회 시간을 오전 10시로 바꿔줘.",
        seed=_seed_sports_day,
        required_tools={"query_event": 1, "update_event": 1},
        forbidden_tools={"create_event", "delete_event"},
        checks=(
            ("9/11 10:00 으로 바뀌었다", lambda s: s.starts_at().startswith("2026-09-11T10:00")),
        ),
    ),
    EvalCase(
        "T12",
        "모레 운동회 전날 저녁 8시에 알려줘.",
        seed=_seed_sports_day,
        required_tools={"query_event": 1, "create_reminder": 1},
        forbidden_tools={"create_event"},
        checks=(
            (
                "알림이 9/10 20:00 이다",
                lambda s: any(x.startswith("2026-09-10T20:00") for x in s.remind_at),
            ),
        ),
    ),
    EvalCase(
        "T13",
        "금요일 오전 10시에 어린이집 물놀이가 있어. 수영복이랑 여벌옷을 챙겨야 해.",
        required_tools={"create_event": 1, "create_event_item": 2},
        expect_parse=False,  # 한 일정 + 그 부속이라 복합이 아니다 (D7)
        checks=(
            ("9/11 10:00 로 저장됐다", lambda s: s.starts_at().startswith("2026-09-11T10:00")),
            ("수영복이 있다", lambda s: any("수영복" in name for name in s.item_names)),
            ("여벌옷이 있다", lambda s: any("여벌옷" in name for name in s.item_names)),
        ),
    ),
    EvalCase(
        "T14",
        "오늘 간식으로 딸기케이크를 먹였어. 저녁에는 뭘 먹이면 좋을까?",
        required_tools={"create_observation_food": 1},
        forbidden_tools={"create_event", "create_observation_activity"},
        expect_parse=True,
        expect_out_of_scope=True,
        checks=(
            (
                "딸기케이크만 저장됐다",
                lambda s: any("딸기케이크" in str(f.get("subject")) for f in s.fields("food")),
            ),
        ),
    ),
    EvalCase(
        "T15",
        "오늘 기록 다 지워줘.",
        seed=_seed_apple,
        forbidden_tools={MUTATING},
        expect_clarification=True,
        checks=(("아무것도 지우지 않았다", lambda s: len(s.rows("food")) == 1),),
    ),
    EvalCase(
        "T16",
        "오늘 2시에 모래놀이했어.",
        required_tools={"create_observation_activity": 1},
        expect_parse=False,
    ),
    EvalCase(
        "T17",
        "오늘 2시에 모래놀이하고 떡볶이 먹었어.",
        required_tools={"create_observation_activity": 1, "create_observation_food": 1},
        expect_parse=True,
    ),
    EvalCase(
        "T18",
        "내일부터 한글 공부를 시작하려고 해.",
        forbidden_tools={MUTATING},
        checks=(("저장 없이 되물었다", lambda s: not s.wrote_anything),),
    ),
    EvalCase(
        "T19",
        "우리 애 우유 알레르기 있어. 등록해줘.",
        forbidden_tools={MUTATING},
        expect_out_of_scope=True,
        checks=(("아무것도 저장하지 않았다", lambda s: not s.wrote_anything),),
    ),
    EvalCase(
        "T20",
        "어제부터 계속 기침하는데 무슨 병일까?",
        required_tools={"create_observation_health": 1},
        expect_out_of_scope=True,
        checks=(
            (
                "기침이 저장됐다",
                lambda s: any(
                    any("기침" in str(x) for x in f.get("symptom", [])) for f in s.fields("health")
                ),
            ),
        ),
    ),
    EvalCase(
        "T21",
        "선생님이 그러는데 오늘 낮에 배가 아프다고 했대.",
        required_tools={"create_observation_health": 1},
        checks=(
            (
                "전해 들은 말로 기록됐다",
                lambda s: all(
                    f.get("confidence_source") == "parent_hearsay" for f in s.fields("health")
                ),
            ),
            _no_guess("health", "observed_time"),
        ),
    ),
    EvalCase(
        "T22",
        "오늘 도서관에서 책 읽고 놀이터에서 그네도 탔어.",
        required_tools={"create_observation_education": 1, "create_observation_activity": 1},
        expect_parse=True,
    ),
    EvalCase(
        "T23",
        "아까 블록놀이 기록 있잖아, 그거 30분으로 고쳐줘.",
        seed=_seed_block_play,
        required_tools={"query_observation_activity": 1, "update_observation_activity": 1},
        forbidden_tools={"create_observation_activity"},
        checks=(
            (
                "30분으로 바뀌었다",
                lambda s: [f.get("duration_min") for f in s.fields("activity")] == [30],
            ),
        ),
    ),
    EvalCase(
        "T24",
        "다음 주 수요일 병원 예약 있어. 진료 전날 밤 9시에 알려줘.",
        required_tools={"create_event": 1, "create_reminder": 1},
        checks=(
            ("병원이 9/16 으로 저장됐다", lambda s: s.starts_at().startswith("2026-09-16")),
            (
                "알림이 9/15 21:00 이다",
                lambda s: any(x.startswith("2026-09-15T21:00") for x in s.remind_at),
            ),
        ),
    ),
    EvalCase(
        "T25",
        "오늘 사과 먹었고 사과를 좋아하는 것 같아.",
        required_tools={"create_observation_food": 1},
        checks=(
            ("한 건만 저장됐다", lambda s: len(s.rows("food")) == 1),
            ("좋아함이 반영됐다", lambda s: all(f.get("polarity") == 1 for f in s.fields("food"))),
        ),
    ),
]


# ── 입력 파일과의 일치 ──────────────────────────────────────────
def _load_inputs() -> dict[str, str]:
    pattern = re.compile(r"^\[(T\d{2})\]\s*(.+)$")
    result: dict[str, str] = {}
    for line in INPUT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = pattern.match(line.strip())
        if not match:
            raise AssertionError(f"test_input.txt 형식 오류: {line}")
        result[match.group(1)] = match.group(2).strip()
    return result


FILE_INPUTS = _load_inputs()
_MISSING = [case.case_id for case in CASES if case.case_id not in FILE_INPUTS]
assert not _MISSING, f"test_input.txt 에 없는 CASES id: {_MISSING}"
for _case in CASES:
    assert _case.text == FILE_INPUTS[_case.case_id], f"{_case.case_id} 입력이 파일과 다릅니다."


# ── 실행 ────────────────────────────────────────────────────────
def _client(model: str | None) -> LLMClient:
    settings = AgentSettings(MEMORY_MODEL=model) if model else AgentSettings()
    if not settings.MEMORY_API_KEY or not settings.MEMORY_BASE_URL:
        pytest.skip("MEMORY_API_KEY / MEMORY_BASE_URL 이 필요합니다. apps/api/.env 를 확인하세요.")
    return LLMClient(settings)


async def _snapshot(case: EvalCase, client: LLMClient) -> Snapshot:
    store = InMemoryStore(now=NOW)
    context = AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)
    if case.seed:
        await case.seed(context)

    result = await run(case.text, context, client=client)

    events = [
        (
            event,
            await store.list_event_items(event_id=event.id),
            await store.list_reminders(event_id=event.id),
        )
        for event in await store.query_events(child_id=CHILD)
    ]
    observations = {
        domain: await store.query_observations(domain=domain, child_id=CHILD) for domain in DOMAINS
    }
    return Snapshot(result=result, observations=observations, events=events)


def _judge(case: EvalCase, snapshot: Snapshot) -> list[str]:
    failures: list[str] = []
    if not snapshot.result.completed:
        failures.append("MAX_STEPS 안에 끝내지 못함")

    for tool, minimum in case.required_tools.items():
        actual = snapshot.count(tool)
        if actual < minimum:
            failures.append(f"{tool}: 최소 {minimum}회 필요, 실제 {actual}회")

    for tool in case.forbidden_tools:
        if tool == MUTATING:
            used = [
                call.name
                for call in snapshot.result.calls
                if call.name.startswith(("create_", "update_", "delete_"))
            ]
        else:
            used = [call.name for call in snapshot.result.calls if call.name == tool]
        if used:
            failures.append(f"금지된 호출: {sorted(set(used))}")

    if case.expect_parse is True and snapshot.count("parse_input") == 0:
        failures.append("복합 입력인데 parse_input 을 부르지 않음")
    if case.expect_parse is False and snapshot.count("parse_input") > 0:
        failures.append("단일 입력인데 parse_input 을 부름")

    if case.expect_clarification and not snapshot.said(_CLARIFY):
        failures.append(f"되묻지 않음: {snapshot.result.final_message!r}")
    if case.expect_out_of_scope and not snapshot.said(_OUT_OF_SCOPE):
        failures.append(f"범위 밖이라고 안내하지 않음: {snapshot.result.final_message!r}")

    for label, predicate in case.checks:
        try:
            if not predicate(snapshot):
                failures.append(label)
        except Exception as exc:  # 저장 자체가 안 되면 검사식이 터진다
            failures.append(f"{label} (검사 실패: {type(exc).__name__})")

    failed = [
        f"{call.name}:{call.result.get('error', {}).get('code')}"
        for call in snapshot.result.calls
        if not call.success
    ]
    if failed:
        failures.append(f"tool 실패 {failed}")
    return failures


@pytest.fixture(scope="session", autouse=True)
def _reset_results() -> None:
    RESULT_PATH.unlink(missing_ok=True)


@pytest.mark.parametrize("model", EVAL_MODELS or [None], ids=lambda m: m or "env")
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_memory_agent_eval(case: EvalCase, model: str | None) -> None:
    client = _client(model)
    started = time.perf_counter()
    snapshot = asyncio.run(_snapshot(case, client))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    failures = _judge(case, snapshot)

    record = {
        "case_id": case.case_id,
        "model": model or "env",
        "passed": not failures,
        "failures": failures,
        "steps": snapshot.result.steps,
        "tools": snapshot.result.tool_names,
        "usage": snapshot.result.usage,
        "latency_ms": elapsed_ms,
        "final_message": snapshot.result.final_message,
    }
    with RESULT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    verdict = "PASS" if not failures else "FAIL"
    print(
        f"\n[{case.case_id}] {verdict} steps={snapshot.result.steps} "
        f"tools={snapshot.result.tool_names}"
    )
    for failure in failures:
        print(f"    - {failure}")

    assert not failures, f"{case.case_id}: " + " / ".join(failures)
