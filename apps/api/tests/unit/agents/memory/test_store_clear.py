"""선택 필드 비우기 — tool 스펙 · 스키마 허용 목록.

store 에 쓰고 다시 읽는 시나리오는 store_contract.py 로 옮겼다 (#257).
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

import app.domains.memory.observation.models  # noqa: F401  observation 테이블을 metadata 에 올림
from app.agents.memory.context import AgentContext
from app.agents.memory.registry import TOOL_SPECS, execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.schemas.common import ClearableUpdateArgs
from app.agents.memory.schemas.observation import (
    ObservationActivityUpdate,
    ObservationEducationUpdate,
    ObservationFoodUpdate,
    ObservationHealthUpdate,
    ObservationRoutineUpdate,
)
from app.agents.memory.schemas.schedule import EventUpdate
from app.agents.memory.store import InMemoryStore
from app.domains.schedule.models import Event
from app.infra.db.base import Base
from tests.unit.agents.memory.store_contract import CLEARABLE as _CLEARABLE

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)

_UPDATE_SCHEMAS: dict[str, type[ClearableUpdateArgs]] = {
    "food": ObservationFoodUpdate,
    "health": ObservationHealthUpdate,
    "education": ObservationEducationUpdate,
    "activity": ObservationActivityUpdate,
    "routine": ObservationRoutineUpdate,
}


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


# ── clear 를 받지 않는 tool ─────────────────────────────────────
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("update_event_item", {"item_id": "event_item-1", "clear": ["item_name"]}),
        (
            "create_observation_food",
            {"raw_text": "원문", "observed_on": "오늘", "subject": "김밥", "clear": ["amount"]},
        ),
    ],
    ids=["update_event_item", "create"],
)
async def test_clear_는_수정_tool_에만_있다(
    context: AgentContext, tool: str, args: dict[str, Any]
) -> None:
    result = await execute_tool(tool, args, context)

    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS


def test_tool_스펙에는_clear_만_나가고_허용_목록은_안_나간다() -> None:
    with_clear = {
        spec["function"]["name"]
        for spec in TOOL_SPECS
        if "clear" in spec["function"]["parameters"]["properties"]
    }

    assert with_clear == {f"update_observation_{domain}" for domain in _CLEARABLE} | {
        "update_event"
    }
    assert all("CLEARABLE" not in str(spec) for spec in TOOL_SPECS)


# ── 허용 목록이 D3 · ORM 과 맞는지 ──────────────────────────────
@pytest.mark.parametrize("domain", list(_CLEARABLE))
def test_스키마의_허용_목록은_D3_와_같다(domain: str) -> None:
    assert _UPDATE_SCHEMAS[domain].CLEARABLE == _CLEARABLE[domain]


@pytest.mark.parametrize("domain", list(_CLEARABLE))
def test_관찰의_허용_목록은_tool_에_열린_nullable_컬럼과_같다(domain: str) -> None:
    table = Base.metadata.tables.get(f"observation_{domain}")
    if table is None:
        pytest.skip("ORM 모델이 아직 없다. 모델이 들어오면 이 대조가 자동으로 돈다")

    exposed = [name for name in _UPDATE_SCHEMAS[domain].model_fields if name in table.columns]
    nullable = {name for name in exposed if table.columns[name].nullable}

    assert _CLEARABLE[domain] == nullable


def test_일정의_허용_목록은_nullable_컬럼_ends_at_하나다() -> None:
    columns = Event.__table__.columns

    assert EventUpdate.CLEARABLE == {"ends_at"}
    assert columns["ends_at"].nullable is True
    # tool 로 고칠 수 있는 나머지 컬럼은 전부 NOT NULL 이다
    assert not any(columns[name].nullable for name in EventUpdate.model_fields if name in columns)
