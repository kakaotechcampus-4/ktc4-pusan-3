"""persisted_write — DB 에 실제로 쓴 Memory 호출만 확정된 저장으로 센다.

쓰기 tool 이 성공했다는 것만으로는 저장됐다고 볼 수 없다.
일정 · 준비물의 create/update는 초안이고, 바뀐 것 없는 성공은 아무것도 쓰지 않는다.
"""

from typing import Any

import pytest

from app.agents.memory.agent import MemoryAgentResult, ToolCallRecord, persisted_write
from app.agents.memory.bundles import LOOKUP_EDIT, MUTATING_PREFIXES, RECORD_BASE

_DOMAINS = ("food", "health", "education", "activity", "routine")
_SAVED = {"id": "obs-1", "observed_on": "2026-09-09"}


def _call(
    name: str, data: dict[str, Any], *, success: bool = True, **arguments: Any
) -> ToolCallRecord:
    result: dict[str, Any] = {"success": success, "operation": "x", "resource": "x"}
    if success:
        result["data"] = data
    else:
        result["error"] = {"code": "X", "message": "x"}
    return ToolCallRecord(name=name, arguments=arguments, result=result)


# (tool, 결과 data, 인자, DB 에 쓴 것인가). 쓰기 tool 마다 적어도 한 줄 있어야 한다
CASES: list[tuple[str, dict[str, Any], dict[str, Any], bool]] = [
    *((f"create_observation_{domain}", _SAVED, {}, True) for domain in _DOMAINS),
    *((f"update_observation_{domain}", _SAVED, {}, True) for domain in _DOMAINS),
    ("update_observation_food", {"id": "obs-1", "status": "deleted"}, {}, True),
    ("create_event", {"draft": True, "title": "운동회"}, {}, False),
    ("update_event", {"id": "event-1", "draft": True, "changed": ["title"]}, {}, False),
    ("update_event", {"id": "event-1", "draft": False, "changed": []}, {}, False),
    ("delete_event", {"id": "event-1"}, {}, True),
    ("create_event_item", {"draft": True, "added": True}, {}, False),
    ("create_event_item", {"draft": False, "added": False}, {}, False),
    ("update_event_item", {"draft": False, "item_id": "item-1"}, {"is_prepared": True}, True),
    # 챙김을 먼저 쓰고 이름은 초안으로 — 결과가 초안이어도 챙김은 이미 썼다
    (
        "update_event_item",
        {"draft": True, "item_id": "item-1"},
        {"item_name": "물통", "is_prepared": True},
        True,
    ),
    ("update_event_item", {"draft": True, "item_id": "item-1"}, {"item_name": "물통"}, False),
    (
        "update_event_item",
        {"draft": False, "item_id": "item-1", "changed": []},
        {"item_name": "물통"},
        False,
    ),
    ("delete_event_item", {"item_id": "item-1"}, {}, True),
]


@pytest.mark.parametrize(("name", "data", "arguments", "expected"), CASES)
def test_DB_에_실제로_쓴_호출만_센다(
    name: str, data: dict[str, Any], arguments: dict[str, Any], expected: bool
) -> None:
    assert persisted_write(_call(name, data, **arguments)) is expected


def test_실패한_호출은_세지_않는다() -> None:
    assert persisted_write(_call("create_observation_food", {}, success=False)) is False


def test_조회는_세지_않는다() -> None:
    assert persisted_write(_call("query_event", {"events": []})) is False


def test_쓰기_tool_은_모두_경우가_있다() -> None:
    # 쓰기 tool 을 더하고 위 표에 경우를 안 넣으면 여기서 깨진다. 초안인지 바로 쓰는지 정해서 넣는다
    mutating = {name for name in RECORD_BASE | LOOKUP_EDIT if name.startswith(MUTATING_PREFIXES)}
    assert mutating <= {name for name, *_ in CASES}


def test_초안만_만든_run_은_persisted_가_아니다() -> None:
    result = MemoryAgentResult(
        reply=None, completed=True, steps=1, calls=[_call("create_event", {"draft": True})]
    )
    assert result.persisted is False
