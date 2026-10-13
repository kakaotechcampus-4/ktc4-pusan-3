"""관찰 삭제 = update 의 status=deleted (soft delete).

행은 store 에 남고 status 만 deleted 로 바뀐다. 지운 행은 조회·수정 대상에서 빠진다.
status 로 열리는 값은 deleted 하나고, 지우는 호출에는 다른 수정을 섞지 않는다.
store 에 쓰고 다시 읽는 시나리오는 store_contract.py 로 옮겼다 (#257).
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.bundles import LOOKUP_EDIT
from app.agents.memory.context import AgentContext
from app.agents.memory.registry import TOOL_SPECS, registered_names
from app.agents.memory.store import InMemoryStore
from tests.unit.agents.memory.store_contract import DOMAINS as _DOMAINS

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


def test_관찰_delete_tool_은_없다() -> None:
    names = set(registered_names()) | {spec["function"]["name"] for spec in TOOL_SPECS}

    assert not any(name.startswith("delete_observation_") for name in names)
    assert not any(name.startswith("delete_observation_") for name in LOOKUP_EDIT)
    assert {f"update_observation_{domain}" for domain in _DOMAINS} <= LOOKUP_EDIT


def test_update_스펙의_status_는_deleted_하나만_연다() -> None:
    for domain in _DOMAINS:
        spec = next(
            spec
            for spec in TOOL_SPECS
            if spec["function"]["name"] == f"update_observation_{domain}"
        )
        status = spec["function"]["parameters"]["properties"]["status"]
        values = {option.get("const") for option in status["anyOf"]} - {None}
        values |= {value for option in status["anyOf"] for value in option.get("enum", [])}
        assert values == {"deleted"}
