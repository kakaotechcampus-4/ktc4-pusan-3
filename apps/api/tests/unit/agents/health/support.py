"""Health 테스트가 같이 쓰는 값과 만드는 함수. 테스트 파일이 아니다(수집되지 않는다)."""

import json
from datetime import UTC, date, datetime
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.common.age import stage_of
from app.agents.common.gate import DataReady, Gate
from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    in_memory_ports,
)
from app.agents.health.store.ports import HealthObservation
from app.rules.age import LifeStage

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
# KST 9/28 01:00 = UTC 9/27 16:00. UTC 날짜로 세면 하루가 어긋나는 시간대다
NOW = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)
# KST 로는 12개월 당일, UTC 로는 11개월
BIRTH_12_IN_KST = date(2025, 9, 28)
BIRTH_48 = date(2022, 9, 28)

REPO_ROOT = Path(__file__).resolve().parents[6]
HEALTH_DOCS = REPO_ROOT / "docs" / "agents" / "health"


def birth_for(months: int, today: date = TODAY) -> date:
    """오늘 기준으로 정확히 `months` 개월인 아이의 생일(일은 오늘과 같다)."""
    total = today.year * 12 + (today.month - 1) - months
    return date(total // 12, total % 12 + 1, today.day)


def gate(months: int, **kwargs) -> Gate:
    """registry 테스트용 Gate. 기본은 동의 있음 · 안전 조회 성공."""
    stage = LifeStage(
        months=months, stage=stage_of(months), big="infant" if months < 12 else "toddler"
    )
    base = dict(stage=stage, consent_child_health=True, safety_ok=True, data=DataReady())
    return Gate(**{**base, **kwargs})


def context(
    birth: date | None = None,
    *,
    consent: bool = True,
    safety: InMemoryHealthSafety | None = None,
    memory: InMemoryHealthMemory | None = None,
    medication: InMemoryMedicationStore | None = None,
) -> HealthContext:
    return HealthContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(
            CHILD,
            birth or BIRTH_48,
            consent=consent,
            safety=safety,
            memory=memory,
            medication=medication,
        ),
    )


_OBSERVATION_IDS = count(1)


def observation(
    day: date,
    *,
    to: date | None = None,
    symptoms: tuple[str, ...] = ("기침",),
    at: datetime | None = None,
) -> HealthObservation:
    """증상 기록 한 건. `to` 가 있으면 `day` ~ `to`(포함) 기간 기록이다."""
    return HealthObservation(
        id=UUID(int=1000 + next(_OBSERVATION_IDS)),
        observed_from=day,
        observed_to=to or day,
        observed_time=at,
        symptoms=symptoms,
    )


def task(label: str | None, text: str = "테스트 요청") -> DomainTask:
    return DomainTask(run_id="run-1", agent="health", task_type=label, request_texts=(text,))


class FakeLLM:
    """tool 호출을 차례로 돌려주는 가짜 모델. `seen` 이 비어 있으면 모델을 0회 부른 것이다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.seen.append([dict(m) for m in messages])
        if self._queue:
            return self._queue.pop(0)
        return LLMResponse(
            message=SimpleNamespace(content="끝", tool_calls=None), usage={}, latency_ms=1
        )


_CALL_IDS = count(1)


def call(name: str, args: dict[str, Any]) -> LLMResponse:
    """모델이 tool 하나를 부르는 응답."""
    tool_call = SimpleNamespace(
        id=f"c{next(_CALL_IDS)}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=[tool_call]), usage={}, latency_ms=1
    )


def calls(*pairs: tuple[str, dict[str, Any]]) -> LLMResponse:
    """모델이 한 응답에 tool 을 여럿 부르는 응답. 루프는 온 순서대로 전부 실행한다."""
    tool_calls = [
        SimpleNamespace(
            id=f"c{next(_CALL_IDS)}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
        )
        for name, args in pairs
    ]
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=tool_calls), usage={}, latency_ms=1
    )
