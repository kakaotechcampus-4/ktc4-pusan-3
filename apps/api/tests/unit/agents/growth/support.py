"""Growth 테스트가 같이 쓰는 값과 만드는 함수. 테스트 파일이 아니다(수집되지 않는다)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.common.gate import DataReady, Gate
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.context import GrowthContext
from app.agents.growth.store.inmemory import in_memory_ports
from app.agents.growth.store.ports import GrowthMeasurement
from app.rules.age import LifeStage, stage_of

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
# KST 9/28 01:00 = UTC 9/27 16:00. UTC 날짜로 세면 하루가 어긋나는 시간대다
NOW = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)
# KST 로는 12개월 당일, UTC 로는 11개월
BIRTH_12_IN_KST = date(2025, 9, 28)
BIRTH_48 = date(2022, 9, 28)

REPO_ROOT = Path(__file__).resolve().parents[6]
GROWTH_DOCS = REPO_ROOT / "docs" / "agents" / "growth"


def birth_for(months: int, today: date = TODAY) -> date:
    """오늘 기준으로 정확히 `months` 개월인 아이의 생일(일은 오늘과 같다)."""
    total = today.year * 12 + (today.month - 1) - months
    return date(total // 12, total % 12 + 1, today.day)


def gate(months: int, **kwargs) -> Gate:
    """registry 테스트용 Gate. 기본은 동의 있음 · 안전 조회 성공 · 공지 없음 · 도서 API 정상."""
    stage = LifeStage(
        months=months, stage=stage_of(months), big="infant" if months < 12 else "toddler"
    )
    base = dict(stage=stage, consent_child_health=True, safety_ok=True, data=DataReady())
    return Gate(**{**base, **kwargs})


def context(birth: date | None = None, *, consent: bool = True, **ports) -> GrowthContext:
    return GrowthContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(CHILD, birth or BIRTH_48, consent=consent, **ports),
    )


def task(label: str | None, text: str = "테스트 요청") -> DomainTask:
    return DomainTask(run_id="run-1", agent="growth", task_type=label, request_texts=(text,))


def measurement(
    n: int, day: date, height: str | None = None, weight: str | None = None
) -> GrowthMeasurement:
    return GrowthMeasurement(
        id=UUID(int=1000 + n),
        child_id=CHILD,
        measured_on=day,
        height_cm=Decimal(height) if height is not None else None,
        weight_kg=Decimal(weight) if weight is not None else None,
    )


def doc_inputs() -> dict[str, str]:
    """`test_input_growth.txt` 의 [Txx] 입력. 문서가 정본이다."""
    lines = (GROWTH_DOCS / "test_input_growth.txt").read_text(encoding="utf-8").splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        if line.startswith("[T") and "] " in line:
            key, text = line[1:].split("] ", 1)
            parsed[key] = text
    return parsed
