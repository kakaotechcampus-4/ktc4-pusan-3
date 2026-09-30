"""관찰 삭제 = update 의 status=deleted (soft delete).

행은 store 에 남고 status 만 deleted 로 바뀐다. 지운 행은 조회·수정 대상에서 빠진다.
status 로 열리는 값은 deleted 하나고, 지우는 호출에는 다른 수정을 섞지 않는다.
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.bundles import LOOKUP_EDIT
from app.agents.memory.context import AgentContext
from app.agents.memory.registry import TOOL_SPECS, execute_tool, registered_names
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)

_DOMAINS = ("food", "health", "education", "activity", "routine")
_CREATE_ARGS: dict[str, dict[str, Any]] = {
    "food": {"subject": "김밥"},
    "health": {"symptom": ["발열"]},
    "education": {"subject": "한글 자모", "topic": "한글 자모 활동지"},
    "activity": {"subject": "레고", "activity": "레고로 성 만들기"},
    "routine": {"subject": "양치하기", "routine_category": "self_care"},
}
# 지우는 호출에 섞으면 안 되는 수정 하나씩
_ANOTHER_CHANGE: dict[str, dict[str, Any]] = {
    "food": {"reaction": "좋아함"},
    "health": {"body_part": "이마"},
    "education": {"duration_min": 30},
    "activity": {"location": "집"},
    "routine": {"trigger": "시켜서"},
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


async def _create(context: AgentContext, domain: str) -> str:
    args = {"raw_text": "원문", "observed_on": "오늘", **_CREATE_ARGS[domain]}
    result = await execute_tool(f"create_observation_{domain}", args, context)
    assert result.success is True, result.error
    return result.data["id"]


async def _delete(context: AgentContext, domain: str, observation_id: str):
    return await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, "status": "deleted"},
        context,
    )


@pytest.mark.parametrize("domain", _DOMAINS)
async def test_status_deleted_로_지우면_행은_남고_조회에서_빠진다(
    context: AgentContext, domain: str
) -> None:
    observation_id = await _create(context, domain)

    result = await _delete(context, domain, observation_id)

    assert result.success is True, result.error
    assert result.data == {"id": observation_id, "status": "deleted"}
    # store 안에는 행이 남아 있다
    stored = context.store._observations[observation_id]
    assert stored.fields["status"] == "deleted"
    assert stored.raw_text == "원문"
    # 조회·단건 조회에서는 빠진다
    assert await context.store.get_observation(domain=domain, observation_id=observation_id) is None
    found = await execute_tool(f"query_observation_{domain}", {}, context)
    assert found.data["count"] == 0


@pytest.mark.parametrize("domain", _DOMAINS)
async def test_지운_기록은_다시_지우거나_고칠_수_없다(context: AgentContext, domain: str) -> None:
    observation_id = await _create(context, domain)
    await _delete(context, domain, observation_id)

    again = await _delete(context, domain, observation_id)
    edit = await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, **_ANOTHER_CHANGE[domain]},
        context,
    )

    assert again.success is False
    assert again.error["code"] == ErrorCode.TARGET_NOT_FOUND
    assert edit.success is False
    assert edit.error["code"] == ErrorCode.TARGET_NOT_FOUND


@pytest.mark.parametrize("domain", _DOMAINS)
@pytest.mark.parametrize("mixed", ["field", "observed_on", "clear"])
async def test_지우는_호출에_다른_수정을_섞으면_거절한다(
    context: AgentContext, domain: str, mixed: str
) -> None:
    observation_id = await _create(context, domain)
    extra: dict[str, Any] = {
        "field": _ANOTHER_CHANGE[domain],
        "observed_on": {"observed_on": "어제"},
        "clear": {"clear": list(_ANOTHER_CHANGE[domain])},
    }[mixed]

    result = await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, "status": "deleted", **extra},
        context,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    row = await context.store.get_observation(domain=domain, observation_id=observation_id)
    assert row is not None and row.fields["status"] == "active"


@pytest.mark.parametrize("status", ["active", "stand_alone", "inactive"])
async def test_update_로는_deleted_말고_다른_status_를_쓸_수_없다(
    context: AgentContext, status: str
) -> None:
    observation_id = await _create(context, "food")

    result = await execute_tool(
        "update_observation_food",
        {"observation_id": observation_id, "status": status},
        context,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS


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
