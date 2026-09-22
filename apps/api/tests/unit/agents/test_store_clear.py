"""선택 필드 비우기 회귀 테스트.

비울 수 있는 필드마다 세 가지로 불러 결과가 갈리는지 본다.

    - 키 없음: 기존 값 유지
    - null 명시: 기존 값 유지
    - clear 지정: 비워짐

"키가 없다" 와 "키는 있는데 null 이다" 는 코드로는 거의 같아 보이는데 결과는 반대여야 한다.
어긋나도 예외가 아니라 성공 + 값 그대로로 끝나서 눈으로 리뷰해서는 안 잡힌다.

허용 목록 검증은 스키마 validator 에 있어서 execute_tool 을 거쳐야 재현된다.
비울 수 있는 필드 목록(_CLEARABLE)은 store_plan D3 의 표 그대로다.
"""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

import app.domains.memory.observation.models  # noqa: F401  observation 테이블을 metadata 에 올림
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
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
from app.agents.memory.store.ports import EventRow
from app.domains.schedule.models import Event
from app.infra.db.base import Base

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)

_CLEARABLE: dict[str, set[str]] = {
    "food": {"action", "amount", "reaction"},
    "health": {"severity", "body_part", "suspected_trigger", "action_taken", "observed_time"},
    "education": {"session_type", "duration_min", "engagement_level"},
    "activity": {"location", "companions", "duration_min", "engagement_level"},
    "routine": {"context", "assistance_level", "completion_status", "trigger"},
}
_UPDATE_SCHEMAS: dict[str, type[ClearableUpdateArgs]] = {
    "food": ObservationFoodUpdate,
    "health": ObservationHealthUpdate,
    "education": ObservationEducationUpdate,
    "activity": ObservationActivityUpdate,
    "routine": ObservationRoutineUpdate,
}
# 비울 수 있는 필드를 전부 채워 만든다. 처음부터 비어 있으면 세 갈래가 구분되지 않는다
_CREATE_ARGS: dict[str, dict[str, Any]] = {
    "food": {"subject": "김밥", "action": "먹었다", "amount": "반 그릇", "reaction": "좋아함"},
    "health": {
        "symptom": ["발열"],
        "severity": "mild",
        "body_part": "이마",
        "suspected_trigger": "우유 먹은 뒤",
        "action_taken": "해열제",
        "observed_time": "오전 8시",
    },
    "education": {
        "subject": "한글 자모",
        "topic": "한글 자모 활동지",
        "session_type": "학습지",
        "duration_min": 30,
        "engagement_level": "high",
    },
    "activity": {
        "subject": "레고",
        "activity": "레고로 성 만들기",
        "location": "집",
        "companions": "혼자",
        "duration_min": 30,
        "engagement_level": "high",
    },
    "routine": {
        "subject": "양치하기",
        "routine_category": "self_care",
        "context": "잠들기 전",
        "assistance_level": "independent",
        "completion_status": "completed",
        "trigger": "시켜서",
    },
}
# 도메인마다 NOT NULL 이라 비울 수 없는 필드 하나
_NOT_CLEARABLE = {
    "food": "subject",
    "health": "symptom",
    "education": "topic",
    "activity": "activity",
    "routine": "subject",
}

_FIELDS = [(domain, name) for domain, names in _CLEARABLE.items() for name in sorted(names)]
_MODES = ["키_없음", "null", "clear"]


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


async def _create_observation(context: AgentContext, domain: str) -> str:
    args = {"raw_text": "원문", "observed_on": "오늘", **_CREATE_ARGS[domain]}
    result = await execute_tool(f"create_observation_{domain}", args, context)
    assert result.success is True, result.error
    return result.data["id"]


async def _fields(context: AgentContext, domain: str, observation_id: str) -> dict[str, Any]:
    row = await context.store.get_observation(domain=domain, observation_id=observation_id)
    assert row is not None
    return row.fields


async def _create_event(context: AgentContext, **args: Any) -> str:
    """create_event 는 초안만 만든다. 수정 케이스가 쓸 행은 그 값으로 직접 시드한다."""
    result = await execute_tool(
        "create_event", {"title": "운동회", "starts_on": "2026-09-17", **args}, context
    )
    assert result.success is True, result.error
    draft = context.drafts.all()[-1]
    row = await context.store.create_event(
        child_id=context.child_id,
        title=draft.title,
        starts_at=draft.starts_at,
        ends_at=draft.ends_at,
        all_day=draft.all_day,
        fields={"event_type": draft.event_type, "category": draft.category},
    )
    return row.id


async def _event(context: AgentContext, event_id: str) -> EventRow | EventDraft:
    """update_event 는 store 를 고치지 않는다. 수정 초안이 있으면 그것을 본다."""
    draft = context.drafts.get(event_id)
    if draft is not None:
        return draft
    row = await context.store.get_event(event_id=event_id)
    assert row is not None
    return row


# ── 관찰: 세 갈래 ─────────────────────────────────────────────────
@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(("domain", "name"), _FIELDS, ids=[f"{d}.{n}" for d, n in _FIELDS])
async def test_관찰은_clear_에_적은_필드만_비워진다(
    context: AgentContext, domain: str, name: str, mode: str
) -> None:
    observation_id = await _create_observation(context, domain)
    before = await _fields(context, domain, observation_id)
    assert before[name] is not None

    args: dict[str, Any] = {"observation_id": observation_id}
    if mode == "null":
        args[name] = None
    elif mode == "clear":
        args["clear"] = [name]
    result = await execute_tool(f"update_observation_{domain}", args, context)

    assert result.success is True, result.error
    after = await _fields(context, domain, observation_id)
    # 비운 필드 말고는 하나도 바뀌지 않는다
    assert after == ({**before, name: None} if mode == "clear" else before)
    assert result.data.get("cleared") == ([name] if mode == "clear" else None)


async def test_여러_필드를_한_번에_비울_수_있다(context: AgentContext) -> None:
    observation_id = await _create_observation(context, "food")

    result = await execute_tool(
        "update_observation_food",
        {"observation_id": observation_id, "clear": ["reaction", "amount"]},
        context,
    )

    assert result.success is True, result.error
    after = await _fields(context, "food", observation_id)
    assert after["amount"] is None and after["reaction"] is None
    assert after["action"] == "먹었다"
    assert result.data["cleared"] == ["amount", "reaction"]


async def test_이슈_재현_관찰_시각을_null_로_보내도_지워지지_않고_clear_로만_지워진다(
    context: AgentContext,
) -> None:
    observation_id = await _create_observation(context, "health")
    recorded = (await _fields(context, "health", observation_id))["observed_time"]

    await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "body_part": "팔", "observed_time": None},
        context,
    )
    assert (await _fields(context, "health", observation_id))["observed_time"] == recorded

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "clear": ["observed_time"]},
        context,
    )
    assert result.data["cleared"] == ["observed_time"]
    after = await _fields(context, "health", observation_id)
    assert after["observed_time"] is None
    assert after["body_part"] == "팔"


async def test_빈_시각_표현은_지우기가_아니다(context: AgentContext) -> None:
    # "  " 는 resolve_time 에서 None 으로 풀린다. None 을 그보다 먼저 거르면 이 값이 지우기가 된다
    observation_id = await _create_observation(context, "health")
    before = await _fields(context, "health", observation_id)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "observed_time": "  "},
        context,
    )

    assert result.success is True, result.error
    assert await _fields(context, "health", observation_id) == before


# ── 관찰: 거부 ──────────────────────────────────────────────────
@pytest.mark.parametrize(("domain", "name"), _NOT_CLEARABLE.items(), ids=list(_NOT_CLEARABLE))
async def test_비울_수_없는_필드를_적으면_거부하고_저장도_바꾸지_않는다(
    context: AgentContext, domain: str, name: str
) -> None:
    observation_id = await _create_observation(context, domain)
    before = await _fields(context, domain, observation_id)

    result = await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, "clear": [name]},
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    # 다시 부를 수 있게 허용 목록을 알려준다
    assert all(allowed in result.error["message"] for allowed in _CLEARABLE[domain])
    assert await _fields(context, domain, observation_id) == before


async def test_거부_문구에_모델이_보낸_이름을_되풀이하지_않는다(context: AgentContext) -> None:
    observation_id = await _create_observation(context, "health")
    leaked = "발화원문처럼보이는문자열"

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "clear": [leaked]},
        context,
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert leaked not in result.error["message"]


async def test_같은_필드에_새_값과_지우기가_같이_오면_거부한다(context: AgentContext) -> None:
    observation_id = await _create_observation(context, "health")
    before = await _fields(context, "health", observation_id)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "severity": "severe", "clear": ["severity"]},
        context,
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert await _fields(context, "health", observation_id) == before


# ── 일정: 종료 ──────────────────────────────────────────────────
_TIMED = {"starts_time": "오후 3시", "ends_time": "오후 5시"}
_MULTI_DAY = {"starts_time": "오전 9시", "ends_on": "2026-09-19", "ends_time": "오후 5시"}


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("base", [_TIMED, _MULTI_DAY], ids=["시각", "여러_날"])
async def test_일정_종료는_clear_로만_지워진다(
    context: AgentContext, base: dict[str, str], mode: str
) -> None:
    event_id = await _create_event(context, **base)
    before = await _event(context, event_id)

    args: dict[str, Any] = {"event_id": event_id, "title": "가을 운동회"}
    if mode == "null":
        args["ends_time"] = None
    elif mode == "clear":
        args["clear"] = ["ends_at"]
    result = await execute_tool("update_event", args, context)

    assert result.success is True, result.error
    after = await _event(context, event_id)
    assert after.title == "가을 운동회"
    assert after.starts_at == before.starts_at
    assert after.ends_at == (None if mode == "clear" else before.ends_at)
    assert result.data.get("cleared") == (["ends_at"] if mode == "clear" else None)


async def test_종료를_지워도_저장된_행은_그대로다(context: AgentContext) -> None:
    event_id = await _create_event(context, **_TIMED)
    saved = await context.store.get_event(event_id=event_id)
    assert saved is not None and saved.ends_at is not None

    await execute_tool("update_event", {"event_id": event_id, "clear": ["ends_at"]}, context)

    still = await context.store.get_event(event_id=event_id)
    assert still is not None and still.ends_at == saved.ends_at
    draft = context.drafts.get(event_id)
    assert draft is not None
    assert draft.ends_at is None
    assert draft.changed == ("ends_at",)


async def test_종일_일정의_종료는_지울_수_없다(context: AgentContext) -> None:
    event_id = await _create_event(context, starts_time="하루 종일")
    before = await _event(context, event_id)

    result = await execute_tool(
        "update_event", {"event_id": event_id, "clear": ["ends_at"]}, context
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert await _event(context, event_id) == before


@pytest.mark.parametrize("extra", [{"ends_on": "2026-09-18"}, {"ends_time": "오후 6시"}])
async def test_종료를_지우면서_새_종료를_주면_거부한다(
    context: AgentContext, extra: dict[str, str]
) -> None:
    event_id = await _create_event(context, **_TIMED)
    before = await _event(context, event_id)

    result = await execute_tool(
        "update_event", {"event_id": event_id, "clear": ["ends_at"], **extra}, context
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert await _event(context, event_id) == before


@pytest.mark.parametrize("name", ["ends_time", "ends_on", "title"])
async def test_일정은_ends_at_말고는_비울_수_없다(context: AgentContext, name: str) -> None:
    event_id = await _create_event(context, **_TIMED)

    result = await execute_tool("update_event", {"event_id": event_id, "clear": [name]}, context)

    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert "ends_at" in result.error["message"]


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
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR


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
