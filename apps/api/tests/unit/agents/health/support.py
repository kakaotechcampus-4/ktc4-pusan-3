"""Health 테스트가 같이 쓰는 값과 만드는 함수. 테스트 파일이 아니다(수집되지 않는다)."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.common.age import stage_of
from app.agents.common.gate import DataReady, Gate
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext
from app.agents.health.store.inmemory import InMemoryHealthSafety, in_memory_ports
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
) -> HealthContext:
    return HealthContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(CHILD, birth or BIRTH_48, consent=consent, safety=safety),
    )


def task(label: str | None, text: str = "테스트 요청") -> DomainTask:
    return DomainTask(run_id="run-1", agent="health", task_type=label, request_texts=(text,))
