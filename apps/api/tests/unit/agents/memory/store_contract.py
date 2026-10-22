"""Memory store 계약 시나리오. InMemoryStore 와 DbMemoryStore 가 같은 결과를 내는지 본다 (#257).

tool(execute_tool)로 쓰고 store 로 다시 읽는다. 시나리오는 어느 store 인지 모르고 Harness 만 받는다.

    단위  tests/unit/agents/memory/test_store_contract.py             InMemoryStore · DB 없음
    통합  tests/integration/agents/memory/test_store_contract_db.py   DbMemoryStore · session 픽스처

InMemoryStore 가 DB 와 다르게 나오면 InMemoryStore 를 DB 에 맞춘다 — 단위 테스트가 실제 저장소와
같은 것을 보게 하려는 것이다. DbMemoryStore 쪽이 틀리면 통합 쪽에서 그 케이스만 xfail(strict) 로
두고 이유를 적는다.

store 를 다시 읽지 않는 검증(tool 스펙 · 스키마 허용 목록)은 test_store_clear.py ·
test_observation_delete.py 에 남아 단위로만 돈다.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store.ports import EventRow

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)


class Harness(Protocol):
    """시나리오가 store 종류를 모르고 쓰는 것."""

    context: AgentContext

    async def seed_event(self, draft: EventDraft) -> str:
        """create_event 가 만든 초안 값으로 확정 일정 행을 넣고 id 를 돌려준다.

        tool 은 일정을 저장하지 않는다(초안까지만). 수정 시나리오가 쓸 행은 이걸로 만든다.
        """
        ...

    async def stored(self, domain: str, observation_id: str) -> tuple[str, str]:
        """(status, raw_text). 지운 행도 읽는다 — store 의 조회는 deleted 를 거른다."""
        ...

    async def seed_item(self, event_id: str, item_name: str) -> str:
        """확정 일정에 준비물을 넣고 id 를 돌려준다. tool 에는 준비물 저장이 없다(초안까지만)."""
        ...

    async def backdate_prepared(self, item_id: str, when: datetime) -> None:
        """체크 시각을 과거로 민다. DB now() 는 테스트 트랜잭션 안에서 늘 같아서 이렇게 구분한다."""
        ...


Scenario = Callable[..., Awaitable[None]]

DOMAINS = ("food", "health", "education", "activity", "routine")

# ── 선택 필드 비우기 ─────────────────────────────────────────────
# 비울 수 있는 필드마다 세 가지로 불러 결과가 갈리는지 본다.
#   키 없음: 기존 값 유지 / null 명시: 기존 값 유지 / clear 지정: 비워짐
# "키가 없다" 와 "키는 있는데 null 이다" 는 코드로는 거의 같아 보이는데 결과는 반대여야 한다.
# 어긋나도 예외가 아니라 성공 + 값 그대로로 끝나서 눈으로 리뷰해서는 안 잡힌다.
# 비울 수 있는 필드 목록(CLEARABLE)은 store_plan D3 의 표 그대로다.
CLEARABLE: dict[str, set[str]] = {
    "food": {"action", "amount", "reaction"},
    "health": {
        "severity",
        "body_part",
        "suspected_trigger",
        "action_taken",
        "observed_time",
        "temperature",
        "measured_at",
        "measure_site",
    },
    "education": {"session_type", "duration_min", "engagement_level"},
    "activity": {"location", "companions", "duration_min", "engagement_level"},
    "routine": {"context", "assistance_level", "completion_status", "trigger"},
}
# 비울 수 있는 필드를 전부 채워 만든다. 처음부터 비어 있으면 세 갈래가 구분되지 않는다
_FULL_ARGS: dict[str, dict[str, Any]] = {
    "food": {"subject": "김밥", "action": "먹었다", "amount": "반 그릇", "reaction": "좋아함"},
    "health": {
        # 체온은 원문에 있는 숫자만 저장된다 (#551)
        "raw_text": "이마가 뜨거워서 오전 8시에 재 보니 38.5도",
        "symptom": ["발열"],
        "severity": "mild",
        "body_part": "이마",
        "suspected_trigger": "우유 먹은 뒤",
        "action_taken": "해열제",
        "observed_time": "오전 8시",
        "temperature": 38.5,
        "measured_at": "오전 8시",
        "measure_site": "ear",
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
_FIELDS = [(domain, name) for domain, names in CLEARABLE.items() for name in sorted(names)]
_MODES = ["키_없음", "null", "clear"]

# 일정 종료
_TIMED = {"starts_time": "오후 3시", "ends_time": "오후 5시"}
_MULTI_DAY = {"starts_time": "오전 9시", "ends_on": "2026-09-19", "ends_time": "오후 5시"}

# ── 관찰 삭제 = update 의 status=deleted (soft delete) ─────────────
# 행은 store 에 남고 status 만 deleted 로 바뀐다. 지운 행은 조회·수정 대상에서 빠진다.
# status 로 열리는 값은 deleted 하나고, 지우는 호출에는 다른 수정을 섞지 않는다.
_MINIMAL_ARGS: dict[str, dict[str, Any]] = {
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


# ── helper ──────────────────────────────────────────────────────
async def _create_observation(h: Harness, domain: str, args: dict[str, Any] | None = None) -> str:
    payload = {"raw_text": "원문", "observed_on": "오늘", **(args or _FULL_ARGS[domain])}
    result = await execute_tool(f"create_observation_{domain}", payload, h.context)
    assert result.success is True, result.error
    return result.data["id"]


async def _fields(h: Harness, domain: str, observation_id: str) -> dict[str, Any]:
    row = await h.context.store.get_observation(domain=domain, observation_id=observation_id)
    assert row is not None
    return row.fields


async def _create_event(h: Harness, **args: Any) -> str:
    result = await execute_tool(
        "create_event", {"title": "운동회", "starts_on": "2026-09-17", **args}, h.context
    )
    assert result.success is True, result.error
    return await h.seed_event(h.context.drafts.all()[-1])


async def _event(h: Harness, event_id: str) -> EventRow | EventDraft:
    """update_event 는 store 를 고치지 않는다. 수정 초안이 있으면 그것을 본다."""
    draft = h.context.drafts.get(event_id)
    if draft is not None:
        return draft
    row = await h.context.store.get_event(event_id=event_id)
    assert row is not None
    return row


async def _delete(h: Harness, domain: str, observation_id: str) -> Any:
    return await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, "status": "deleted"},
        h.context,
    )


# ── 관찰: 세 갈래 ─────────────────────────────────────────────────
async def 관찰은_clear_에_적은_필드만_비워진다(
    h: Harness, *, domain: str, name: str, mode: str
) -> None:
    observation_id = await _create_observation(h, domain)
    before = await _fields(h, domain, observation_id)
    assert before[name] is not None

    args: dict[str, Any] = {"observation_id": observation_id}
    if mode == "null":
        args[name] = None
    elif mode == "clear":
        args["clear"] = [name]
    result = await execute_tool(f"update_observation_{domain}", args, h.context)

    assert result.success is True, result.error
    after = await _fields(h, domain, observation_id)
    # 비운 필드 말고는 하나도 바뀌지 않는다
    assert after == ({**before, name: None} if mode == "clear" else before)
    assert result.data.get("cleared") == ([name] if mode == "clear" else None)


async def 여러_필드를_한_번에_비울_수_있다(h: Harness) -> None:
    observation_id = await _create_observation(h, "food")

    result = await execute_tool(
        "update_observation_food",
        {"observation_id": observation_id, "clear": ["reaction", "amount"]},
        h.context,
    )

    assert result.success is True, result.error
    after = await _fields(h, "food", observation_id)
    assert after["amount"] is None and after["reaction"] is None
    assert after["action"] == "먹었다"
    assert result.data["cleared"] == ["amount", "reaction"]


async def 이슈_재현_관찰_시각을_null_로_보내도_지워지지_않고_clear_로만_지워진다(
    h: Harness,
) -> None:
    observation_id = await _create_observation(h, "health")
    recorded = (await _fields(h, "health", observation_id))["observed_time"]

    await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "body_part": "팔", "observed_time": None},
        h.context,
    )
    assert (await _fields(h, "health", observation_id))["observed_time"] == recorded

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "clear": ["observed_time"]},
        h.context,
    )
    assert result.data["cleared"] == ["observed_time"]
    after = await _fields(h, "health", observation_id)
    assert after["observed_time"] is None
    assert after["body_part"] == "팔"


async def 날짜만_고쳐도_관찰_시각이_새_날짜를_따라간다(
    h: Harness, *, time_args: dict[str, Any]
) -> None:
    # observed_time 은 날짜까지 품은 timestamptz 다.
    # 날짜만 고치고 두면 observed_range 와 다른 날을 가리킨다 (#485)
    observation_id = await _create_observation(h, "health")

    result = await execute_tool(
        "update_observation_health",
        {
            "observation_id": observation_id,
            "observed_on": "어제",
            "temporal_direction": "past",
            **time_args,
        },
        h.context,
    )

    assert result.success is True, result.error
    after = await _fields(h, "health", observation_id)
    assert after["observed_time"] == datetime(2026, 9, 14, 8, 0, tzinfo=KST)


async def 빈_시각_표현은_지우기가_아니다(h: Harness) -> None:
    # "  " 는 resolve_time 에서 None 으로 풀린다. None 을 그보다 먼저 거르면 이 값이 지우기가 된다
    observation_id = await _create_observation(h, "health")
    before = await _fields(h, "health", observation_id)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "observed_time": "  "},
        h.context,
    )

    assert result.success is True, result.error
    assert await _fields(h, "health", observation_id) == before


# ── 관찰: 거부 ──────────────────────────────────────────────────
async def 비울_수_없는_필드를_적으면_거부하고_저장도_바꾸지_않는다(
    h: Harness, *, domain: str, name: str
) -> None:
    observation_id = await _create_observation(h, domain)
    before = await _fields(h, domain, observation_id)

    result = await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, "clear": [name]},
        h.context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    # 다시 부를 수 있게 허용 목록을 알려준다
    assert all(allowed in result.error["message"] for allowed in CLEARABLE[domain])
    assert await _fields(h, domain, observation_id) == before


async def 거부_문구에_모델이_보낸_이름을_되풀이하지_않는다(h: Harness) -> None:
    observation_id = await _create_observation(h, "health")
    leaked = "발화원문처럼보이는문자열"

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "clear": [leaked]},
        h.context,
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert leaked not in result.error["message"]


async def 같은_필드에_새_값과_지우기가_같이_오면_거부한다(h: Harness) -> None:
    observation_id = await _create_observation(h, "health")
    before = await _fields(h, "health", observation_id)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": observation_id, "severity": "severe", "clear": ["severity"]},
        h.context,
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert await _fields(h, "health", observation_id) == before


# ── 일정: 종료 ──────────────────────────────────────────────────
async def 일정_종료는_clear_로만_지워진다(h: Harness, *, base: dict[str, str], mode: str) -> None:
    event_id = await _create_event(h, **base)
    before = await _event(h, event_id)

    args: dict[str, Any] = {"event_id": event_id, "title": "가을 운동회"}
    if mode == "null":
        args["ends_time"] = None
    elif mode == "clear":
        args["clear"] = ["ends_at"]
    result = await execute_tool("update_event", args, h.context)

    assert result.success is True, result.error
    after = await _event(h, event_id)
    assert after.title == "가을 운동회"
    assert after.starts_at == before.starts_at
    assert after.ends_at == (None if mode == "clear" else before.ends_at)
    assert result.data.get("cleared") == (["ends_at"] if mode == "clear" else None)


async def 종료를_지워도_저장된_행은_그대로다(h: Harness) -> None:
    # 지우는 것도 보호자가 제출해야 반영된다
    event_id = await _create_event(h, **_TIMED)
    saved = await h.context.store.get_event(event_id=event_id)
    assert saved is not None and saved.ends_at is not None

    await execute_tool("update_event", {"event_id": event_id, "clear": ["ends_at"]}, h.context)

    still = await h.context.store.get_event(event_id=event_id)
    assert still is not None and still.ends_at == saved.ends_at
    draft = h.context.drafts.get(event_id)
    assert draft is not None
    assert draft.ends_at is None
    assert draft.changed == ("ends_at",)


async def 종일_일정의_종료는_지울_수_없다(h: Harness) -> None:
    event_id = await _create_event(h, starts_time="하루 종일")
    before = await _event(h, event_id)

    result = await execute_tool(
        "update_event", {"event_id": event_id, "clear": ["ends_at"]}, h.context
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert await _event(h, event_id) == before


async def 종료를_지우면서_새_종료를_주면_거부한다(h: Harness, *, extra: dict[str, str]) -> None:
    event_id = await _create_event(h, **_TIMED)
    before = await _event(h, event_id)

    result = await execute_tool(
        "update_event", {"event_id": event_id, "clear": ["ends_at"], **extra}, h.context
    )

    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert await _event(h, event_id) == before


async def 일정은_ends_at_말고는_비울_수_없다(h: Harness, *, name: str) -> None:
    event_id = await _create_event(h, **_TIMED)

    result = await execute_tool("update_event", {"event_id": event_id, "clear": [name]}, h.context)

    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "ends_at" in result.error["message"]


# ── 관찰 삭제 ───────────────────────────────────────────────────
async def status_deleted_로_지우면_행은_남고_조회에서_빠진다(h: Harness, *, domain: str) -> None:
    observation_id = await _create_observation(h, domain, _MINIMAL_ARGS[domain])

    result = await _delete(h, domain, observation_id)

    assert result.success is True, result.error
    assert result.data == {"id": observation_id, "status": "deleted"}
    # store 안에는 행이 남아 있다
    assert await h.stored(domain, observation_id) == ("deleted", "원문")
    # 조회·단건 조회에서는 빠진다
    found_one = await h.context.store.get_observation(domain=domain, observation_id=observation_id)
    assert found_one is None
    found = await execute_tool(f"query_observation_{domain}", {}, h.context)
    assert found.data["count"] == 0


async def 지운_기록은_다시_지우거나_고칠_수_없다(h: Harness, *, domain: str) -> None:
    observation_id = await _create_observation(h, domain, _MINIMAL_ARGS[domain])
    await _delete(h, domain, observation_id)

    again = await _delete(h, domain, observation_id)
    edit = await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, **_ANOTHER_CHANGE[domain]},
        h.context,
    )

    assert again.success is False
    assert again.error["code"] == ErrorCode.TARGET_NOT_FOUND
    assert edit.success is False
    assert edit.error["code"] == ErrorCode.TARGET_NOT_FOUND


async def 지우는_호출에_다른_수정을_섞으면_거절한다(h: Harness, *, domain: str, mixed: str) -> None:
    observation_id = await _create_observation(h, domain, _MINIMAL_ARGS[domain])
    extra: dict[str, Any] = {
        "field": _ANOTHER_CHANGE[domain],
        "observed_on": {"observed_on": "어제"},
        "clear": {"clear": list(_ANOTHER_CHANGE[domain])},
    }[mixed]

    result = await execute_tool(
        f"update_observation_{domain}",
        {"observation_id": observation_id, "status": "deleted", **extra},
        h.context,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    row = await h.context.store.get_observation(domain=domain, observation_id=observation_id)
    assert row is not None and row.fields["status"] == "active"


async def update_로는_deleted_말고_다른_status_를_쓸_수_없다(h: Harness, *, status: str) -> None:
    observation_id = await _create_observation(h, "food", _MINIMAL_ARGS["food"])

    result = await execute_tool(
        "update_observation_food",
        {"observation_id": observation_id, "status": status},
        h.context,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS


# ── 준비물 챙김 ─────────────────────────────────────────────────
# 체크 시각 규칙은 백엔드 PATCH /children/{cid}/event-items/{iid} 와 같다 (#335).
#   미체크 → 체크: 지금 시각 / 체크 → 다시 체크: 처음 시각 유지 / 해제: None / 해제 → 해제: None
async def store_의_챙김_시각은_처음_체크한_때를_지킨다(h: Harness) -> None:
    event_id = await _create_event(h, **_TIMED)
    item_id = await h.seed_item(event_id, "체육복")
    store = h.context.store

    first = await store.set_event_item_prepared(item_id=item_id, is_prepared=True)
    assert first is not None and first.is_prepared and first.prepared_at is not None

    earlier = NOW - timedelta(days=1)
    await h.backdate_prepared(item_id, earlier)
    again = await store.set_event_item_prepared(item_id=item_id, is_prepared=True)
    assert again is not None and again.prepared_at == earlier

    off = await store.set_event_item_prepared(item_id=item_id, is_prepared=False)
    assert off is not None and off.is_prepared is False and off.prepared_at is None
    off_again = await store.set_event_item_prepared(item_id=item_id, is_prepared=False)
    assert off_again is not None and off_again.prepared_at is None

    back = await store.set_event_item_prepared(item_id=item_id, is_prepared=True)
    assert back is not None and back.prepared_at is not None and back.prepared_at != earlier


async def 없는_준비물을_챙기면_None_이다(h: Harness) -> None:
    # 형식이 틀린 id 도 예외가 아니라 None
    for item_id in ("없는-준비물", "00000000-0000-0000-0000-000000000000"):
        row = await h.context.store.set_event_item_prepared(item_id=item_id, is_prepared=True)
        assert row is None


async def tool_로_다시_체크해도_처음_시각을_지킨다(h: Harness) -> None:
    event_id = await _create_event(h, **_TIMED)
    item_id = await h.seed_item(event_id, "체육복")
    check = {"item_id": item_id, "is_prepared": True}

    first = await execute_tool("update_event_item", check, h.context)
    assert first.success is True, first.error

    earlier = NOW - timedelta(days=1)
    await h.backdate_prepared(item_id, earlier)
    await execute_tool("update_event_item", check, h.context)
    row = await h.context.store.get_event_item(item_id=item_id)
    assert row is not None and row.is_prepared and row.prepared_at == earlier


# ── 케이스 목록 ──────────────────────────────────────────────────
def _case(scenario: Scenario, label: str = "", **params: Any) -> Any:
    case_id = f"{scenario.__name__}-{label}" if label else scenario.__name__
    return pytest.param(scenario, params, id=case_id)


CASES = [
    *(
        _case(관찰은_clear_에_적은_필드만_비워진다, f"{d}.{n}-{m}", domain=d, name=n, mode=m)
        for d, n in _FIELDS
        for m in _MODES
    ),
    _case(여러_필드를_한_번에_비울_수_있다),
    _case(이슈_재현_관찰_시각을_null_로_보내도_지워지지_않고_clear_로만_지워진다),
    # 빈 시각 표현은 "시각은 안 바꿈" 이라 키 없음과 같아야 한다
    *(
        _case(날짜만_고쳐도_관찰_시각이_새_날짜를_따라간다, label, time_args=args)
        for label, args in (
            ("키_없음", {}),
            ("null", {"observed_time": None}),
            ("빈_표현", {"observed_time": "  "}),
        )
    ),
    _case(빈_시각_표현은_지우기가_아니다),
    *(
        _case(비울_수_없는_필드를_적으면_거부하고_저장도_바꾸지_않는다, d, domain=d, name=n)
        for d, n in _NOT_CLEARABLE.items()
    ),
    _case(거부_문구에_모델이_보낸_이름을_되풀이하지_않는다),
    _case(같은_필드에_새_값과_지우기가_같이_오면_거부한다),
    *(
        _case(일정_종료는_clear_로만_지워진다, f"{label}-{m}", base=base, mode=m)
        for label, base in (("시각", _TIMED), ("여러_날", _MULTI_DAY))
        for m in _MODES
    ),
    _case(종료를_지워도_저장된_행은_그대로다),
    _case(종일_일정의_종료는_지울_수_없다),
    *(
        _case(종료를_지우면서_새_종료를_주면_거부한다, next(iter(extra)), extra=extra)
        for extra in ({"ends_on": "2026-09-18"}, {"ends_time": "오후 6시"})
    ),
    *(
        _case(일정은_ends_at_말고는_비울_수_없다, n, name=n)
        for n in ("ends_time", "ends_on", "title")
    ),
    *(_case(status_deleted_로_지우면_행은_남고_조회에서_빠진다, d, domain=d) for d in DOMAINS),
    *(_case(지운_기록은_다시_지우거나_고칠_수_없다, d, domain=d) for d in DOMAINS),
    *(
        _case(지우는_호출에_다른_수정을_섞으면_거절한다, f"{d}-{x}", domain=d, mixed=x)
        for d in DOMAINS
        for x in ("field", "observed_on", "clear")
    ),
    *(
        _case(update_로는_deleted_말고_다른_status_를_쓸_수_없다, s, status=s)
        for s in ("active", "stand_alone", "inactive")
    ),
    _case(store_의_챙김_시각은_처음_체크한_때를_지킨다),
    _case(없는_준비물을_챙기면_None_이다),
    _case(tool_로_다시_체크해도_처음_시각을_지킨다),
]
