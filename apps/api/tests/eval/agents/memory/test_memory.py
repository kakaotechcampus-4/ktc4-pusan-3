"""Memory Agent 라이브 eval. 실제 모델을 부르고 run()을 돌린다.
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/memory/test_memory.py -m live -s

Memory 하나만 떼어 T01~T30 의 저장 결과를 세밀하게 본다. Supervisor → Memory → Food 를
끝까지 돌리는 라이브 테스트는 tests/eval/agents/supervisor/test.py 다.

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
MEMORY_API_KEY / MEMORY_BASE_URL / MEMORY_MODEL 은 apps/api/.env 에서 읽는다.

프로토타입과 달라진 점 — 이 파일은 프롬프트도 tool 스펙도 루프도 갖고 있지 않다.
run() 을 부르고 저장된 결과를 본다. 사본을 측정하면 구현을 고쳐도 점수가 안 변한다.

인자를 직접 검사하지 않는 이유: 날짜·시각은 모델이 표현만 주고 코드가 확정한다.
"모델이 날짜를 맞게 계산했는가" 가 아니라 "운동회가 모레로 저장됐는가"를 확인해야 한다.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
from app.agents.memory.drafts import EventDraft
from app.agents.memory.schemas.task import MemoryTask
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import EventItemRow, ObservationRow
from app.agents.supervisor.agent import SupervisorResult
from app.agents.supervisor.routing import Routing, route
from tests.eval.agents.routing_cases import CASES_BY_ID, answer_output

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
# 기준 시각은 **오늘 오전 9시**다. 날짜를 박아 두면 모델이 보는 "현재 시각" 이 실제와 달라지고,
# "오늘"·"모레" 가 지난 날짜로 저장된다. 기대값은 아래 _date()·_weekday() 로 오늘에서 계산한다.
#   $env:EVAL_NOW="2026-09-09"      결과를 나란히 비교할 때 날짜를 고정한다
#   $env:EVAL_NOW_MINUTE="7"        run 간 프롬프트 캐시를 볼 때 분만 바꾼다 (Step 7 합격선)
_PINNED = os.getenv("EVAL_NOW")
TODAY = date.fromisoformat(_PINNED) if _PINNED else datetime.now(KST).date()


def _at(offset: int, hour: int = 9, minute: int = 0) -> datetime:
    """오늘 기준 며칠 뒤의 그 시각 (KST)."""
    day = TODAY + timedelta(days=offset)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


NOW = _at(0, minute=int(os.getenv("EVAL_NOW_MINUTE", "0")))


def _date(offset: int) -> str:
    """오늘 기준 며칠 뒤의 ISO 날짜. "오늘"·"어제"·"모레" 기대값을 만든다."""
    return (TODAY + timedelta(days=offset)).isoformat()


def _weekday(name: str, *, weeks: int = 0) -> str:
    """다가올 그 요일. 오늘은 후보에서 뺀다 (datetime_rules 와 같은 규칙).

    weeks=1 이면 "다음 주 그 요일" — 이번 주 월요일에서 센다.
    """
    target = "월화수목금토일".index(name)
    if weeks:
        monday = TODAY - timedelta(days=TODAY.weekday())
        return (monday + timedelta(days=weeks * 7 + target)).isoformat()
    return (TODAY + timedelta(days=(target - TODAY.weekday()) % 7 or 7)).isoformat()


CHILD = UUID("00000000-0000-7000-8000-000000000001")
WRITER = UUID("00000000-0000-7000-8000-0000000000ff")

DOMAINS = ("food", "health", "education", "activity", "routine")
MUTATING = "쓰기"  # forbidden_tools 에 넣으면 create/update/delete 전부를 금지한다

# solo — task 없이 전체 tool 로. task — Supervisor 정답 출력으로 만든 MemoryTask 로.
# task 모드는 "Supervisor 가 완벽하다고 가정한 Memory 만의 성적" 이다 (supervisor_plan Step 7)
#   $env:EVAL_MEMORY_MODE="task"; uv run pytest tests/eval/agents/test_memory.py -m live -s
# tool 이 거절하고 모델이 고쳐 부르는 것까지는 통과로 본다. 호출 하나가 더 붙는 값이라 수를 센다
#   $env:EVAL_RETRY_BUDGET="0"       한 번도 안 틀려야 통과
RETRY_BUDGET = int(os.getenv("EVAL_RETRY_BUDGET", "1"))

MEMORY_MODE = os.getenv("EVAL_MEMORY_MODE", "solo")
if MEMORY_MODE not in ("solo", "task"):
    raise ValueError(f"EVAL_MEMORY_MODE 는 solo 또는 task: {MEMORY_MODE!r}")

# 모드마다 파일을 따로 둔다 — 나중 run 이 앞 run 의 결과를 지우면 두 모드를 나란히 못 본다
_RESULT_FILE = "eval_results.jsonl" if MEMORY_MODE == "solo" else "eval_task_results.jsonl"
RESULT_PATH = Path(os.getenv("EVAL_RESULT_PATH", str(Path(__file__).with_name(_RESULT_FILE))))
# 입력은 agent 별 폴더 위, tests/eval/agents 에 있다
_INPUT_DEFAULT = Path(__file__).resolve().parents[1] / "test_input.txt"
INPUT_PATH = Path(os.getenv("EVAL_INPUT_PATH", str(_INPUT_DEFAULT)))
EVAL_MODELS = [x.strip() for x in os.getenv("EVAL_MODELS", "").split(",") if x.strip()]

# 되묻는 말은 어미가 실행마다 다르다. "할까요" 만 두면 "지울까요"·"맞나요" 가 떨어진다 —
# 동작은 맞는데 판정이 틀리는 경우다. 물음 어미를 묶어서 본다
_CLARIFY = (
    "무엇을",
    "어떤",
    "몇 시",
    "알려주",
    "말씀해",
    "확인이 필요",
    "골라",
    "선택",
    "까요",  # 할까요 · 지울까요 · 맞을까요
    "나요",  # 인가요 · 맞나요 · 시작하나요
    "정해 주",  # "시간을 정해 주세요" 도 되묻는 말이다
)
# 모델이 쓰는 말이 실행마다 조금씩 다르다. "직접 입력" 하나만 두면 "직접 등록해 주세요" 가
# 떨어진다. (동작은 맞는데 판정이 틀리는 경우라 표현을 넓힘)
_OUT_OF_SCOPE = (
    "범위",
    "추천",
    "진단",
    "직접 입력",
    "직접 등록",
    "직접 추가",
    "할 수 없",
    "하지 않",
    "드릴 수 없",
    "어려워",
)

# 모델이 내부 추론을 그대로 final_message에 흘리는 경우를 잡는다.
# 영문 단어가 다섯 개 넘게 이어지면 한국어 답변이 아니다.
_REASONING_LEAK = re.compile(r"[A-Za-z]{2,}(?:[ ,.'\"?!:;()]+[A-Za-z]{2,}){4,}")


# ── 결과 스냅샷 ─────────────────────────────────────────────────
@dataclass
class Snapshot:
    """run() 이 끝난 뒤의 호출 흔적 + 저장된 상태."""

    result: MemoryAgentResult
    observations: dict[str, list[ObservationRow]]
    drafts: tuple[EventDraft, ...]  # 일정은 저장되지 않으니 제출 전 초안을 본다
    stored_items: list[EventItemRow]  # 승인 없이 바로 쓰는 것(is_prepared)을 볼 곳

    def count(self, tool: str) -> int:
        return sum(1 for call in self.result.calls if call.name == tool)

    def rows(self, domain: str) -> list[ObservationRow]:
        return self.observations[domain]

    def fields(self, domain: str) -> list[dict[str, Any]]:
        return [row.fields for row in self.rows(domain)]

    def observed_on(self, domain: str) -> list[str]:
        return [row.observed_on.isoformat() for row in self.rows(domain)]

    def starts_at(self) -> str:
        first = self.drafts[0] if self.drafts else None
        return first.starts_at.astimezone(KST).isoformat() if first else ""

    @property
    def item_names(self) -> list[str]:
        return [item.item_name for draft in self.drafts for item in draft.items]

    @property
    def wrote_anything(self) -> bool:
        return any(self.observations[domain] for domain in DOMAINS) or bool(self.drafts)

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
    expect_clarification: bool = False
    expect_out_of_scope: bool = False
    checks: tuple[Check, ...] = ()


# ── seed — 조회·수정·삭제 케이스가 기댈 기존 기록 ───────────────
_YESTERDAY = TODAY - timedelta(days=1)


async def _seed_block_play(context: AgentContext) -> None:
    await context.store.create_observation(
        domain="activity",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="어제 블록놀이를 20분 했어",
        observed_on=_YESTERDAY,
        observed_range=DateRange(start=_YESTERDAY, end=TODAY),
        fields={"activity": "블록놀이", "subject": "블록놀이", "duration_min": 20},
    )


async def _seed_today_two_domains(context: AgentContext) -> None:
    """T15 — "오늘 기록 다 지워줘" 가 모호하려면 오늘 기록이 여럿 있어야 한다.

    어제 것만 있으면 "오늘 지울 기록이 없다" 가 맞는 답이라 되묻기를 잴 수 없다 (실측).
    """
    await context.store.create_observation(
        domain="food",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="오늘 점심에 김밥 먹었어",
        observed_on=TODAY,
        observed_range=DateRange(start=TODAY, end=TODAY + timedelta(days=1)),
        fields={"subject": "김밥", "action": "먹었다"},
    )
    await context.store.create_observation(
        domain="activity",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="오늘 놀이터에서 그네 탔어",
        observed_on=TODAY,
        observed_range=DateRange(start=TODAY, end=TODAY + timedelta(days=1)),
        fields={"subject": "그네", "activity": "그네 타기"},
    )


async def _seed_apple(context: AgentContext) -> None:
    await context.store.create_observation(
        domain="food",
        child_id=CHILD,
        source_writer=WRITER,
        raw_text="어제 사과를 먹었어",
        observed_on=_YESTERDAY,
        observed_range=DateRange(start=_YESTERDAY, end=TODAY),
        fields={"subject": "사과", "action": "먹었다"},
    )


async def _seed_sports_day(context: AgentContext) -> None:
    await context.store.create_event(
        child_id=CHILD,
        title="운동회",
        starts_at=_at(2),  # 모레 오전 9시
        ends_at=None,
        # 시각이 있으니 all_day 가 아니다. all_day 는 하루 종일 행사(00:00~23:59)
        all_day=False,
        fields={"created_by": "caregiver", "category": "institution"},
    )


async def _seed_sports_day_items(context: AgentContext) -> None:
    """운동회 + 이미 저장된 준비물 하나. 준비물 경로를 재는 케이스가 쓴다."""
    await _seed_sports_day(context)
    events = await context.store.query_events(child_id=CHILD)
    await context.store.create_event_item(event_id=events[0].id, item_name="체육복")


def _no_guess(domain: str, key: str) -> Check:
    return (
        f"{domain}.{key} 를 지어내지 않았다",
        lambda s: all(f.get(key) in (None, "") for f in s.fields(domain)),
    )


def _routine(category: str) -> Check:
    return (
        f"routine 이 {category} 로 저장됐다",
        lambda s: (
            bool(s.rows("routine"))
            and all(f.get("routine_category") == category for f in s.fields("routine"))
        ),
    )


# 일정이 등록되면 알림을 받기로 한 보호자에게 자동으로 간다고 안내한다
_REMINDER_NOTICE: Check = (
    "일정으로 알림이 간다고 안내했다",
    lambda s: s.said(("알림", "알려")) and s.said(("일정", "등록", "자동")),
)


# ── 케이스 ──────────────────────────────────────────────────────
CASES: list[EvalCase] = [
    EvalCase(
        "T01",
        "오늘 민준이가 아침에 사과를 반 개 먹었어.",
        required_tools={"create_observation_food": 1},
        forbidden_tools={"create_observation_activity", "create_event"},
        checks=(
            (
                "사과가 저장됐다",
                lambda s: any("사과" in str(f.get("subject")) for f in s.fields("food")),
            ),
            (
                "양이 보존됐다",
                lambda s: any("반" in str(f.get("amount")) for f in s.fields("food")),
            ),
            ("오늘로 기록됐다", lambda s: s.observed_on("food") == [_date(0)]),
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
        "모레 오후 3시에 운동회가 있고 체육복을 가져가야 해. "
        "운동회 알림은 내일 오전 8시에 만들어줘. 그리고 내일 일정도 알려줘.",
        required_tools={
            "create_observation_food": 1,
            "create_observation_activity": 1,
            "create_event": 1,
            "query_event": 1,
        },
        # 알림 tool 은 없고, 알림 요청 때문에 일정을 고치지도 않는다.
        # 새 일정의 준비물은 create_event 의 items 로 간다 — 뒤이어 부를 것이 없다
        forbidden_tools={"create_reminder", "update_event", "create_event_item"},
        checks=(
            (
                "운동회 초안이 모레 15:00 이다",
                lambda s: s.starts_at().startswith(f"{_date(2)}T15:00"),
            ),
            ("체육복이 준비물에 있다", lambda s: any("체육복" in name for name in s.item_names)),
            (
                "하루 종일이 1440 이다",
                lambda s: any(f.get("duration_min") == 1440 for f in s.fields("activity")),
            ),
            _REMINDER_NOTICE,
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
            ("초안이 모레 10:00 이다", lambda s: s.starts_at().startswith(f"{_date(2)}T10:00")),
            ("바뀐 필드가 starts_at 이다", lambda s: "starts_at" in s.drafts[0].changed),
        ),
    ),
    EvalCase(
        "T12",
        "모레 운동회 전날 저녁 8시에 알려줘.",
        seed=_seed_sports_day,
        # 알림 요청으로는 일정을 만들지도 고치지도 않는다.
        # 라이브에서 모델이 알림 시각(전날 저녁 8시)에 맞춰 update_event 를 부른 적이 있다
        forbidden_tools={MUTATING},
        checks=(_REMINDER_NOTICE,),
    ),
    EvalCase(
        "T13",
        "금요일 오전 10시에 어린이집 물놀이가 있어. 수영복이랑 여벌옷을 챙겨야 해.",
        required_tools={"create_event": 1},
        # 준비물 둘이 create_event의 items로 한 번에 와야 한다
        forbidden_tools={"create_event_item"},
        checks=(
            (
                "금요일 10:00 초안이다",
                lambda s: s.starts_at().startswith(f"{_weekday('금')}T10:00"),
            ),
            ("초안이 하나다", lambda s: len(s.drafts) == 1),
            ("수영복이 있다", lambda s: any("수영복" in name for name in s.item_names)),
            ("여벌옷이 있다", lambda s: any("여벌옷" in name for name in s.item_names)),
        ),
    ),
    EvalCase(
        "T14",
        "오늘 간식으로 딸기케이크를 먹였어. 저녁에는 뭘 먹이면 좋을까?",
        required_tools={"create_observation_food": 1},
        forbidden_tools={"create_event", "create_observation_activity"},
        # 저녁 메뉴 요청은 범위 밖이 아니다 — Supervisor 가 Food 로 떼어 보낸다.
        # Memory 가 할 일은 간식을 저장하고 그 조각은 건드리지 않는 것뿐이다
        checks=(
            (
                "딸기케이크만 저장됐다",
                lambda s: [f.get("subject") for f in s.fields("food")] == ["딸기케이크"],
            ),
        ),
    ),
    EvalCase(
        "T15",
        "오늘 기록 다 지워줘.",
        seed=_seed_today_two_domains,
        forbidden_tools={MUTATING},
        expect_clarification=True,
        checks=(
            (
                "아무것도 지우지 않았다",
                lambda s: (len(s.rows("food")), len(s.rows("activity"))) == (1, 1),
            ),
        ),
    ),
    EvalCase(
        "T16",
        "오늘 2시에 모래놀이했어.",
        required_tools={"create_observation_activity": 1},
    ),
    EvalCase(
        "T17",
        "오늘 2시에 모래놀이하고 떡볶이 먹었어.",
        required_tools={"create_observation_activity": 1, "create_observation_food": 1},
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
        # 진료가 몇 시인지 모르면 알림이 언제 갈지도 정해지지 않는다. 등록 전에 되묻는다
        forbidden_tools={MUTATING},
        expect_clarification=True,
        checks=(
            ("저장하지 않고 되물었다", lambda s: not s.wrote_anything),
            (
                # 알림 시각은 보호자가 이미 말했고, 우리는 그 시각을 쓰지도 않는다 (D9).
                # 물어야 할 것은 진료가 몇 시인지다. 실측에서 반대로 물었다
                "알림 시각이 아니라 예약 시각을 물었다",
                lambda s: (
                    not s.said(("알림을 보낼 시간", "알림 시간", "알림 시각", "알림을 몇 시"))
                ),
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
    # ── routine (data_model.md §5) — 생활 행동과 activity·food·health 의 경계 ──
    EvalCase(
        "T26",
        "오늘 혼자 양치하고 잠옷도 입었어.",
        required_tools={"create_observation_routine": 1},
        forbidden_tools={"create_observation_activity", "create_observation_health"},
        checks=(
            _routine("self_care"),
            (
                # "잠옷도 입었어" 에는 혼자라는 말이 없다. 거기까지 채우면 지어낸 것이다
                "혼자 했다고 말한 쪽이 independent 다",
                lambda s: any(
                    f.get("assistance_level") == "independent" for f in s.fields("routine")
                ),
            ),
        ),
    ),
    EvalCase(
        "T27",
        "밥 먹을 때 포크만 쓰고 브로콜리는 다 남겼어.",
        required_tools={"create_observation_routine": 1, "create_observation_food": 1},
        checks=(
            _routine("mealtime"),
            (
                "브로콜리는 food 에 있다",
                lambda s: any("브로콜리" in str(f.get("subject")) for f in s.fields("food")),
            ),
            (
                "같은 사실을 겹쳐 저장하지 않았다",
                lambda s: (
                    not any("브로콜리" in str(f.get("subject")) for f in s.fields("routine"))
                    and not any("포크" in str(f.get("subject")) for f in s.fields("food"))
                ),
            ),
        ),
    ),
    EvalCase(
        "T28",
        "오늘 긴장했는지 손톱을 계속 물어뜯었어.",
        required_tools={"create_observation_routine": 1},
        forbidden_tools={"create_observation_health"},  # 습관은 증상이 아니다
        checks=(_routine("habit"), _no_guess("routine", "assistance_level")),
    ),
    EvalCase(
        "T29",
        "장난감 정리하라고 하면 혼자 잘 정리해.",
        required_tools={"create_observation_routine": 1},
        forbidden_tools={"create_observation_activity"},  # 정리는 놀이가 아니다
        checks=(
            _routine("household_task"),
            (
                "시켜야 하는 것이 verbal_prompt 다",
                lambda s: all(
                    f.get("assistance_level") == "verbal_prompt" for f in s.fields("routine")
                ),
            ),
        ),
    ),
    EvalCase(
        "T30",
        "블록으로 성 만들고 나서 정리는 안 하겠대.",
        required_tools={"create_observation_activity": 1, "create_observation_routine": 1},
        checks=(
            _routine("household_task"),
            (
                "거부가 refused 다",
                lambda s: all(f.get("completion_status") == "refused" for f in s.fields("routine")),
            ),
            # confidence_source 는 채점하지 않는다. "안 하겠대" 는 아이의 발언을 옮긴 것이라
            # 스키마 기준(아이의 발언 = parent_direct)과 hearsay 어느 쪽으로도 읽힌다
        ),
    ),
    EvalCase(
        "T31",
        "운동회에 물통이랑 모자도 챙겨야 해.",
        seed=_seed_sports_day,
        # 이미 있는 일정을 찾아 그 일정의 수정 초안에 준비물을 붙인다
        required_tools={"query_event": 1, "create_event_item": 2},
        forbidden_tools={"create_event"},
        checks=(
            ("초안이 하나다", lambda s: len(s.drafts) == 1),
            ("수정 초안이다", lambda s: s.drafts[0].op == "update"),
            ("물통이 있다", lambda s: any("물통" in name for name in s.item_names)),
            ("모자가 있다", lambda s: any("모자" in name for name in s.item_names)),
            (
                "시각은 그대로 모레 09:00 이다",
                lambda s: s.starts_at().startswith(f"{_date(2)}T09:00"),
            ),
            ("바뀐 필드가 items 뿐이다", lambda s: s.drafts[0].changed == ("items",)),
        ),
    ),
    EvalCase(
        "T32",
        "운동회에 물통이랑 모자도 챙기고, 시간은 오전 10시로 바꿔줘.",
        seed=_seed_sports_day,
        # 수정과 준비물 추가가 한 일정에 같이
        required_tools={"query_event": 1, "update_event": 1, "create_event_item": 2},
        forbidden_tools={"create_event"},
        checks=(
            ("초안이 하나다", lambda s: len(s.drafts) == 1),
            ("모레 10:00 이다", lambda s: s.starts_at().startswith(f"{_date(2)}T10:00")),
            ("물통과 모자가 둘 다 있다", lambda s: {"물통", "모자"} <= set(s.item_names)),
            (
                "바뀐 필드가 starts_at 과 items 다",
                lambda s: s.drafts[0].changed == ("starts_at", "items"),
            ),
        ),
    ),
    EvalCase(
        "T33",
        "운동회 준비물 체육복을 체육복 상의로 바꿔줘.",
        seed=_seed_sports_day_items,
        # 준비물 이름 변경은 보호자가 초안에서 확인
        required_tools={"query_event": 1, "update_event_item": 1},
        forbidden_tools={"create_event", "create_event_item", "delete_event_item"},
        checks=(
            ("초안이 하나다", lambda s: len(s.drafts) == 1),
            ("초안 이름이 바뀌었다", lambda s: any("상의" in name for name in s.item_names)),
            ("바뀐 필드가 items 뿐이다", lambda s: s.drafts[0].changed == ("items",)),
            (
                "저장된 이름은 그대로다",
                lambda s: [item.item_name for item in s.stored_items] == ["체육복"],
            ),
        ),
    ),
    EvalCase(
        "T34",
        "운동회 준비물 체육복 챙겼다고 체크해줘.",
        seed=_seed_sports_day_items,
        # 체크는 되돌릴 수 있어 승인 게이트에 넣지 않고 바로 쓴다
        required_tools={"query_event": 1, "update_event_item": 1},
        forbidden_tools={"create_event", "update_event", "create_event_item"},
        checks=(
            ("초안을 만들지 않았다", lambda s: s.drafts == ()),
            ("바로 체크됐다", lambda s: all(item.is_prepared for item in s.stored_items)),
            (
                "챙긴 시각이 남았다",
                lambda s: all(item.prepared_at == NOW for item in s.stored_items),
            ),
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
    return LLMClient(settings, role="memory")


async def _snapshot(case: EvalCase, client: LLMClient) -> Snapshot:
    store = InMemoryStore(now=NOW)
    context = AgentContext(child_id=CHILD, source_writer=WRITER, now=NOW, timezone=KST, store=store)
    if case.seed:
        await case.seed(context)

    task = _task_for(case) if MEMORY_MODE == "task" else None
    result = await run(case.text, context, client=client, task=task)

    observations = {
        domain: await store.query_observations(domain=domain, child_id=CHILD) for domain in DOMAINS
    }
    stored_items = [
        item
        for event in await store.query_events(child_id=CHILD)
        for item in await store.list_event_items(event_id=event.id)
    ]
    return Snapshot(
        result=result,
        observations=observations,
        drafts=result.drafts,
        stored_items=stored_items,
    )


def _routing_for(case: EvalCase) -> Routing:
    """이 케이스의 정답 Supervisor 출력(routing_cases)을 routing 규칙에 통과시킨다.

    실제 Supervisor 를 부르지 않는다 — Supervisor 오류를 빼고 Memory 만 잰다.
    """
    answer = answer_output(CASES_BY_ID[case.case_id])
    return route(case.text, SupervisorResult(output=answer), run_id=f"eval-{case.case_id}")


def _task_for(case: EvalCase) -> MemoryTask:
    """Memory 에 넘길 작업. 요청 조각은 여기 들어오지 않는다 — 그건 도메인 Agent 몫이다."""
    routing = _routing_for(case)
    # 파이프라인은 순수 요청형에서 Memory 를 건너뛴다(SKIP_MEMORY_FOR_PURE_REQUEST).
    # 여기서는 그래도 Memory 를 돌린다 — 불렸을 때 무엇을 하는지가 이 eval 이 재는 것이고,
    # T19("알레르기 등록해줘")처럼 저장하면 안 되는 케이스가 회귀 감시 대상이다.
    # 힌트는 어차피 비어 있어서 건너뛰기 전과 같은 입력이 된다
    return routing.memory_task or MemoryTask(raw_text=case.text)


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

    message = snapshot.result.final_message or ""
    if _REASONING_LEAK.search(message):
        # 기대 문구가 어딘가 들어 있으면 통과하던 자리라, 답변이 엉망이어도 못 잡았다
        failures.append(f"내부 추론이 답변에 섞임: {message[:80]!r}")

    # task 모드에서 범위 밖 안내는 Supervisor(routing guidance) 몫이라 Memory 에게 묻지 않는다
    if MEMORY_MODE == "solo" and case.expect_out_of_scope and not snapshot.said(_OUT_OF_SCOPE):
        failures.append(f"범위 밖이라고 안내하지 않음: {snapshot.result.final_message!r}")

    if case.expect_clarification and not snapshot.said(_CLARIFY):
        failures.append(f"되묻지 않음: {snapshot.result.final_message!r}")

    for label, predicate in case.checks:
        try:
            if not predicate(snapshot):
                failures.append(label)
        except Exception as exc:  # 저장 자체가 안 되면 검사식이 터진다
            failures.append(f"{label} (검사 실패: {type(exc).__name__})")

    # 실패한 호출은 예산 안이면 통과로 본다 — 모델이 error.message 를 보고 고쳐 부르는 건
    # 설계된 경로다(D4). 다만 한 번 고쳐 부를 때마다 모델 호출이 하나 더 붙으므로 (S8) 수를 센다.
    # 예산을 넘겼거나 끝내 못 고쳤으면(저장 결과 검사가 위에서 이미 잡는다) 실패다
    retried = _retried(snapshot)
    if len(retried) > RETRY_BUDGET:
        failures.append(f"재시도 예산({RETRY_BUDGET}회) 초과: {retried}")
    return failures


def _retried(snapshot: Snapshot) -> list[str]:
    return [
        f"{call.name}:{call.result.get('error', {}).get('code')}"
        for call in snapshot.result.calls
        if not call.success
    ]


@pytest.fixture(scope="session", autouse=True)
def _reset_results() -> None:
    RESULT_PATH.unlink(missing_ok=True)


def _expected_text(case: EvalCase) -> str:
    """이 케이스가 통과하려면 무엇이 맞아야 하는가. 결과 파일의 [기대] 가 된다 (conftest).

    _judge 가 보는 것을 그대로 옮긴다 — 여기가 어긋나면 기대와 판정이 다른 말을 하게 된다.
    """
    parts = []
    if case.required_tools:
        parts.append(
            " · ".join(f"{tool} {count}회 이상" for tool, count in case.required_tools.items())
        )
    if case.forbidden_tools:
        names = sorted(
            "쓰기 전부(create·update·delete)" if tool == MUTATING else tool
            for tool in case.forbidden_tools
        )
        parts.append(f"금지 {' · '.join(names)}")
    if case.expect_clarification:
        parts.append("되묻는다")
    if case.expect_out_of_scope:
        parts.append("범위 밖이라고 안내한다 (solo 모드에서만 본다)")
    parts.extend(label for label, _ in case.checks)
    parts.append(f"tool 거절은 {RETRY_BUDGET}회까지 (고쳐 부르면 통과)")
    # 요청 조각이 어디로 갔는지 남긴다 — 이 파일은 Memory 만 돌리므로 그 조각의 "동작" 은
    # 여기 결과에 안 보인다. 끝까지 도는 것은 test.py 다
    for task in _routing_for(case).food_tasks:
        parts.append(
            f"요청 조각 {len(task.request_texts)}개는 Food({task.task_type}) 로 간다"
            " — 이 파일은 Memory 만 돌린다 (끝까지는 test.py)"
        )
    return " / ".join(parts)


@pytest.mark.parametrize("model", EVAL_MODELS or [None], ids=lambda m: m or "env")
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_memory_agent_eval(case: EvalCase, model: str | None, expect: Callable[..., None]) -> None:
    expect(_expected_text(case))
    client = _client(model)
    started = time.perf_counter()
    snapshot = asyncio.run(_snapshot(case, client))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    failures = _judge(case, snapshot)
    retried = _retried(snapshot)

    record = {
        "case_id": case.case_id,
        "model": model or "env",
        "mode": MEMORY_MODE,
        "passed": not failures,
        "failures": failures,
        # 통과해도 남긴다 — 예산 안에서 몇 번 고쳐 불렀는지가 프롬프트·스키마를 고칠 근거다
        "retried": retried,
        "steps": snapshot.result.steps,
        "ended_by": snapshot.result.ended_by,
        "tools": snapshot.result.tool_names,
        "usage": snapshot.result.usage,
        "latency_ms": elapsed_ms,
        "final_message": snapshot.result.final_message,
    }
    with RESULT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    verdict = "PASS" if not failures else "FAIL"
    print(
        f"\n[{case.case_id}] {verdict} {MEMORY_MODE} steps={snapshot.result.steps} "
        f"ended_by={snapshot.result.ended_by} tools={snapshot.result.tool_names}"
        f"\n    usage={snapshot.result.usage} latency_ms={elapsed_ms}"
    )
    if retried:  # 통과해도 보이게 — 예산 안에서 고쳐 부른 것도 프롬프트·스키마를 고칠 거리다
        print(f"    재시도 {len(retried)}/{RETRY_BUDGET}회: {retried}")
    for failure in failures:
        print(f"    - {failure}")

    assert not failures, f"{case.case_id}: " + " / ".join(failures)
