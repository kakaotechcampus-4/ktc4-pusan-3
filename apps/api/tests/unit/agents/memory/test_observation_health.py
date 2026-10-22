"""observation_health 의 체온 세 칸과 먹인 약 두 칸 (#551).

체온:
    - "밤 11시에 38.5도" 는 temperature · measured_at · measure_site 세 칸으로 들어간다
    - 잰 부위를 말하지 않았으면 measure_site 는 비운다
    - 30.0 ~ 45.0 밖의 체온은 저장하지 않고 모델에게 되돌린다

먹인 약 — 한 행 = 한 사건:
    - 증상과 약을 한 번에 받으면 코드가 두 행으로 나누고 약 행에만 연결을 채운다
    - 약만 말하면 약 행 하나, 증상은 비고 연결도 없다
    - 증상도 약도 없으면 저장하지 않는다
    - 약 이름이 raw_text 에 글자 그대로 없으면 저장하지 않는다(성분명으로 바꿔 쓰기 금지)
    - 수정으로 증상 행에 약을 넣거나 증상을 비우면 실패한다

발화 대조 (#551):
    - 체온 숫자도 보호자가 한 말에 있어야 저장한다(반올림 · 화씨 환산 금지). "38도 5부" 는 38.5 다
    - 발화를 모르는 수정 호출은 약 이름 · 체온을 고치지 않는다(대조할 글이 없으면 통과시키지 않는다)
    - 같은 run 에서 증상 · 약은 따로 센다 — 증상을 저장한 뒤 같은 증상 + 약으로 다시 부르면 막는다

가짜 LLM 응답으로 돌리므로 실제 API 를 부르지 않는다. 발화는 지어낸 예시다.
"""

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.memory.agent import run
from app.agents.memory.context import AgentContext
from app.agents.memory.prompt import build_system_prompt
from app.agents.memory.registry import TOOL_SPECS, execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.schemas.task import PendingMemoryContext, WorkType
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=KST)  # 어제 = 2026-09-08


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


class FakeLLM:
    """정해둔 응답을 순서대로 돌려준다. 다 쓰면 마무리 멘트를 낸다."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        if self._queue:
            return self._queue.pop(0)
        return _reply("기록해 둘게요.")


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    raw = json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


def _tools(*calls: SimpleNamespace) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=list(calls)),
        usage={"prompt_tokens": 100, "completion_tokens": 20},
        latency_ms=1,
    )


def _reply(content: str) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=content, tool_calls=None),
        usage={"prompt_tokens": 100, "completion_tokens": 20},
        latency_ms=1,
    )


async def _rows(context: AgentContext) -> list[Any]:
    return await context.store.query_observations(domain="health", child_id=context.child_id)


async def _create(context: AgentContext, **arguments: Any):
    return await execute_tool("create_observation_health", arguments, context)


def said(context: AgentContext, utterance: str) -> AgentContext:
    """이번 run 의 발화를 실은 context — run() 이 채우는 값이다. 같은 store 를 본다."""
    return replace(context, utterance=utterance)


# ── 체온 세 칸 ──────────────────────────────────────────────────
_FEVER_NIGHT = {
    "raw_text": "어젯밤 11시에 귀로 재니까 열이 38.5도였어",
    "observed_on": "어제",
    "temporal_direction": "past",
    "symptom": ["발열"],
    "temperature": 38.5,
    "measured_at": "밤 11시",
    "measure_site": "ear",
}


async def test_밤_11시에_38_5도는_세_칸으로(context: AgentContext) -> None:
    # T20 — 숫자는 말한 그대로, 시각은 observed_time 과 같은 규칙으로 그날에 얹는다
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", _FEVER_NIGHT)))

    result = await run(_FEVER_NIGHT["raw_text"], context, client=llm)

    assert [call.success for call in result.calls] == [True]
    [row] = await _rows(context)
    assert row.fields["temperature"] == Decimal("38.5")
    assert row.fields["measured_at"] == datetime(2026, 9, 8, 23, 0, tzinfo=KST)
    assert row.fields["measure_site"] == "ear"
    assert row.fields["observed_time"] is None  # 잰 시각과 관찰 시각은 다른 칸이다


async def test_부위가_없으면_measure_site_는_비운다(context: AgentContext) -> None:
    arguments = {key: value for key, value in _FEVER_NIGHT.items() if key != "measure_site"}
    arguments["raw_text"] = "어젯밤 11시에 열이 38.5도였어"
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", arguments)))

    await run(arguments["raw_text"], context, client=llm)

    [row] = await _rows(context)
    assert row.fields["measure_site"] is None
    assert row.fields["temperature"] == Decimal("38.5")


async def test_체온_범위_밖이면_저장하지_않는다(context: AgentContext) -> None:
    # 단위를 빼먹은 "385" 처럼 말이 안 되는 값은 고쳐 쓰지 않고 모델에게 되돌린다 → 되묻는다
    arguments = {**_FEVER_NIGHT, "temperature": 385}
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", arguments)))

    result = await run(arguments["raw_text"], context, client=llm)

    [call] = result.calls
    assert call.success is False
    assert call.result["error"]["code"] == ErrorCode.INVALID_ARGS
    assert "temperature" in call.result["error"]["message"]
    assert "없는 필드" not in call.result["error"]["message"]  # 칸이 없어서가 아니라 범위라서
    assert await _rows(context) == []


async def test_measure_site_는_정한_다섯_값만_받는다(context: AgentContext) -> None:
    result = await _create(context, **{**_FEVER_NIGHT, "measure_site": "발바닥"})

    assert result.success is False
    assert result.error is not None
    assert "measure_site" in result.error["message"]
    assert "없는 필드" not in result.error["message"]
    assert await _rows(context) == []


def test_체온_칸은_tool_스펙에_열려_있다() -> None:
    [spec] = [s for s in TOOL_SPECS if s["function"]["name"] == "create_observation_health"]
    properties = spec["function"]["parameters"]["properties"]
    assert {"temperature", "measured_at", "measure_site", "medication_given", "given_time"} <= (
        properties.keys()
    )
    # 연결은 코드가 채운다 — 모델이 id 를 골라 잇지 않는다
    assert "linked_symptom_id" not in properties
    [update] = [s for s in TOOL_SPECS if s["function"]["name"] == "update_observation_health"]
    assert "linked_symptom_id" not in update["function"]["parameters"]["properties"]


# ── 먹인 약 두 칸 ───────────────────────────────────────────────
_FEVER_AND_MEDICINE = {
    "raw_text": "새벽 3시에 열이 끓길래 바로 해열제 먹였어",
    "observed_on": "오늘",
    "symptom": ["발열"],
    "observed_time": "새벽 3시",
    "medication_given": "해열제",
    "given_time": "새벽 3시",
}


async def test_증상과_약을_같이_받으면_두_행_약_행에만_연결(context: AgentContext) -> None:
    result = await _create(context, **_FEVER_AND_MEDICINE)

    assert result.success is True
    symptom_row, medicine_row = await _rows(context)
    assert result.data["id"] == symptom_row.id
    assert result.data["medication_id"] == medicine_row.id

    assert symptom_row.fields["symptom"] == ["발열"]
    assert symptom_row.fields.get("medication_given") is None
    assert symptom_row.fields.get("linked_symptom_id") is None

    assert medicine_row.fields["symptom"] == []
    assert medicine_row.fields["medication_given"] == "해열제"
    assert medicine_row.fields["linked_symptom_id"] == symptom_row.id
    # 약 행의 시각은 먹인 시각이다 — 같은 observed_time 칸을 쓴다
    assert medicine_row.fields["observed_time"] == datetime(2026, 9, 9, 3, 0, tzinfo=KST)
    assert medicine_row.raw_text == symptom_row.raw_text


async def test_약만_말하면_약_행_하나_연결_없음(context: AgentContext) -> None:
    # 따로 온 "해열제 먹였어" 를 직전 열 기록에 잇지 않는다 — 이유를 말하지 않았다
    await _create(
        context,
        raw_text="오늘 아침 7시에 열 나서 쟀어",
        observed_on="오늘",
        symptom=["발열"],
        observed_time="오전 7시",
    )

    result = await _create(
        context,
        raw_text="새벽 3시에 해열제 먹였어",
        observed_on="오늘",
        medication_given="해열제",
        given_time="새벽 3시",
    )

    assert result.success is True
    assert "medication_id" not in result.data
    medicine_rows = [row for row in await _rows(context) if row.fields.get("medication_given")]
    [row] = medicine_rows
    assert row.id == result.data["id"]
    assert row.fields["symptom"] == []
    assert row.fields.get("linked_symptom_id") is None
    assert row.fields["observed_time"] == datetime(2026, 9, 9, 3, 0, tzinfo=KST)


async def test_먹인_시각을_말하지_않으면_비운다(context: AgentContext) -> None:
    await _create(context, raw_text="해열제 먹였어", observed_on="오늘", medication_given="해열제")

    [row] = await _rows(context)
    assert row.fields["observed_time"] is None


async def test_증상도_약도_없으면_실패(context: AgentContext) -> None:
    result = await _create(context, raw_text="오늘 좀 그랬어", observed_on="오늘")

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "medication_given" in result.error["message"]  # 약만 말한 경우도 있다고 알려 준다
    assert await _rows(context) == []


async def test_약_이름이_원문에_없으면_저장하지_않는다(context: AgentContext) -> None:
    # "타이레놀" 을 성분명 "아세트아미노펜" 으로 바꿔 쓰면 보호자가 말하지 않은 사실이 된다
    result = await _create(
        context,
        raw_text="밤에 타이레놀 먹였어",
        observed_on="오늘",
        medication_given="아세트아미노펜",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "raw_text" in result.error["message"]
    # 실패 메시지는 로그로 흘러간다 — 약 이름을 싣지 않는다
    assert "타이레놀" not in result.error["message"]
    assert "아세트아미노펜" not in result.error["message"]
    assert await _rows(context) == []


async def test_약만_말했는데_증상_칸을_채우면_실패(context: AgentContext) -> None:
    # 증상 행이 없으면 체온·부위·시각을 얹을 곳이 없다. 먹인 시각은 given_time 이다
    result = await _create(
        context,
        raw_text="새벽 3시에 해열제 먹였어",
        observed_on="오늘",
        medication_given="해열제",
        observed_time="새벽 3시",
    )

    assert result.success is False
    assert result.error is not None
    assert "given_time" in result.error["message"]
    assert await _rows(context) == []


async def test_수정으로_증상_행에_약을_넣으면_실패(context: AgentContext) -> None:
    created = await _create(context, raw_text="열이 나", observed_on="오늘", symptom=["발열"])

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "medication_given": "해열제"},
        said(context, "해열제 먹였어"),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "create_observation_health" in result.error["message"]
    [row] = await _rows(context)
    assert row.fields.get("medication_given") is None


async def test_수정으로_약_행에_증상을_넣으면_실패(context: AgentContext) -> None:
    created = await _create(
        context, raw_text="해열제 먹였어", observed_on="오늘", medication_given="해열제"
    )

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "symptom": ["발열"]},
        context,
    )

    assert result.success is False
    [row] = await _rows(context)
    assert row.fields["symptom"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"temperature": 38.5},
        {"measured_at": "밤 11시"},
        {"measure_site": "ear"},
        {"severity": "severe"},
        {"body_part": "이마"},
        {"suspected_trigger": "우유 먹은 뒤"},
        {"action_taken": "찜질"},
    ],
)
async def test_수정으로_약_행에_증상_칸을_넣으면_실패(context: AgentContext, change) -> None:
    # 약 행에 체온이 붙으면 열 타임라인이 그 행을 체온 줄과 먹인 약 줄로 두 번 낸다 (최종 리뷰 F2)
    created = await _create(
        context, raw_text="해열제 먹였어", observed_on="오늘", medication_given="해열제"
    )

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], **change},
        said(context, "38.5도였어"),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "kind=symptom" in result.error["message"]
    [row] = await _rows(context)
    assert all(row.fields.get(name) is None for name in change)


async def test_수정으로_약_행의_먹인_시각은_고칠_수_있다(context: AgentContext) -> None:
    # observed_time 은 약 행에선 먹인 시각이다 — 증상 전용 칸이 아니다
    created = await _create(
        context, raw_text="해열제 먹였어", observed_on="오늘", medication_given="해열제"
    )

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "observed_time": "새벽 3시"},
        context,
    )

    assert result.success is True
    [row] = await _rows(context)
    assert row.fields["observed_time"] == datetime(2026, 9, 9, 3, 0, tzinfo=KST)


async def test_수정으로_증상_행의_체온은_고칠_수_있다(context: AgentContext) -> None:
    created = await _create(context, **_FEVER_NIGHT)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "temperature": 38.9, "severity": "severe"},
        said(context, "아니 38.9도였고 많이 힘들어했어"),
    )

    assert result.success is True


async def test_수정으로_증상을_비우면_실패(context: AgentContext) -> None:
    created = await _create(context, raw_text="열이 나", observed_on="오늘", symptom=["발열"])

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "symptom": []},
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    [row] = await _rows(context)
    assert row.fields["symptom"] == ["발열"]


async def test_수정으로_잰_시각을_바꾸면_원래_관찰_날짜에_얹는다(context: AgentContext) -> None:
    created = await _create(context, **_FEVER_NIGHT)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "measured_at": "밤 10시", "temperature": 38.2},
        said(context, "밤 10시에 38.2도였어"),
    )

    assert result.success is True
    [row] = await _rows(context)
    assert row.fields["measured_at"] == datetime(2026, 9, 8, 22, 0, tzinfo=KST)
    assert row.fields["temperature"] == Decimal("38.2")


async def test_수정으로_체온_세_칸을_비울_수_있다(context: AgentContext) -> None:
    created = await _create(context, **_FEVER_NIGHT)

    result = await execute_tool(
        "update_observation_health",
        {
            "observation_id": created.data["id"],
            "clear": ["temperature", "measured_at", "measure_site"],
        },
        context,
    )

    assert result.success is True
    [row] = await _rows(context)
    assert row.fields["temperature"] is None
    assert row.fields["measured_at"] is None
    assert row.fields["measure_site"] is None


def test_프롬프트가_체온과_먹인_약을_넣는_법을_적는다(context: AgentContext) -> None:
    prompt = build_system_prompt(context)

    assert "temperature 에 숫자만" in prompt
    assert "단위를 바꾸지 않는다" in prompt
    assert "잰 부위가 말에 있을 때만 measure_site" in prompt
    # 해열제를 먹였다는 말에서 발열을 지어내지 않는다
    assert "해열제를 먹였다고 발열을 넣지 않는다" in prompt
    assert "약 이름은 말한 그대로" in prompt
    assert "먹인 시각은 말했을 때만" in prompt


# ── 약 이름은 보호자가 실제로 한 말에서 (리뷰 1차) ─────────────────
# raw_text 는 모델이 옮겨 적는 칸이라, 모델이 raw_text 와 medication_given 을 같이 바꿔 쓰면
# raw_text 대조만으로는 못 막는다. 이번 run 의 발화(run 에 들어온 글)가 기준이다.
def _done() -> LLMResponse:
    reply = {"pending_hint": None, "leftover": False, "text": "기록해 둘게요.", "kind": "message"}
    return _reply(json.dumps(reply, ensure_ascii=False))


async def test_원문과_약_이름을_같이_바꿔_써도_발화에_없으면_저장하지_않는다(
    context: AgentContext,
) -> None:
    rewritten = {
        "raw_text": "새벽에 아세트아미노펜 먹였어",
        "observed_on": "오늘",
        "medication_given": "아세트아미노펜",
    }
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", rewritten)))

    result = await run("새벽에 타이레놀 먹였어", context, client=llm)

    [call] = result.calls
    assert call.success is False
    assert call.result["error"]["code"] == ErrorCode.INVALID_ARGS
    assert "아세트아미노펜" not in call.result["error"]["message"]
    assert await _rows(context) == []


async def test_발화에_있는_약_이름은_저장한다(context: AgentContext) -> None:
    said = {
        "raw_text": "새벽에 타이레놀 먹였어",
        "observed_on": "오늘",
        "medication_given": "타이레놀",
    }
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", said)))

    result = await run(said["raw_text"], context, client=llm)

    assert [call.success for call in result.calls] == [True]
    [row] = await _rows(context)
    assert row.fields["medication_given"] == "타이레놀"


async def _medicine_row(context: AgentContext) -> str:
    created = await _create(
        context, raw_text="새벽에 타이레놀 먹였어", observed_on="오늘", medication_given="타이레놀"
    )
    return created.data["id"]


async def test_수정으로_바꾼_약_이름이_발화에_없으면_고치지_않는다(context: AgentContext) -> None:
    observation_id = await _medicine_row(context)
    change = {"observation_id": observation_id, "medication_given": "이부프로펜"}
    llm = FakeLLM(_tools(_call("u1", "update_observation_health", change)))

    result = await run("아까 그 약 부루펜이었어", context, client=llm)

    [call] = result.calls
    assert call.success is False
    assert call.result["error"]["code"] == ErrorCode.INVALID_ARGS
    [row] = await _rows(context)
    assert row.fields["medication_given"] == "타이레놀"


async def test_수정으로_바꾼_약_이름이_발화에_있으면_고친다(context: AgentContext) -> None:
    # "타이레놀 말고 부루펜" — 새 이름은 원래 raw_text 에는 없고 이번 발화에 있다
    observation_id = await _medicine_row(context)
    change = {"observation_id": observation_id, "medication_given": "부루펜"}
    llm = FakeLLM(_tools(_call("u1", "update_observation_health", change)))

    result = await run("타이레놀 말고 부루펜이었어", context, client=llm)

    assert [call.success for call in result.calls] == [True]
    [row] = await _rows(context)
    assert row.fields["medication_given"] == "부루펜"


async def test_이어받기는_조각과_보호자_답에서_약_이름을_찾는다(context: AgentContext) -> None:
    pending = PendingMemoryContext(
        hint_text="새벽에 해열제 먹였어", question="몇 시쯤이었어요?", work=WorkType.OBSERVE
    )
    said = {
        "raw_text": "새벽에 해열제 먹였어",
        "observed_on": "오늘",
        "medication_given": "해열제",
        "given_time": "새벽 3시",
    }
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", said)), _done())

    result = await run("3시", context, client=llm, continuation=pending)

    assert [call.success for call in result.calls] == [True]


async def test_이어받기에서_Memory_가_물은_말의_약_이름은_근거가_아니다(
    context: AgentContext,
) -> None:
    # 질문은 모델이 쓴 글이다. "타이레놀이었어요?" "응" 으로 약 이름이 생기지 않는다
    pending = PendingMemoryContext(
        hint_text="새벽에 약 먹였어",
        question="몇 시쯤이었어요?",
        work=WorkType.OBSERVE,
        transcript=("어떤 약이었어요? 타이레놀이었어요?", "응"),
    )
    # 모델이 질문의 약 이름을 raw_text 에도 옮겨 적은 경우
    said = {
        "raw_text": "새벽에 타이레놀 먹였어",
        "observed_on": "오늘",
        "medication_given": "타이레놀",
    }
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", said)), _done())

    result = await run("3시", context, client=llm, continuation=pending)

    assert result.calls[0].success is False
    assert await _rows(context) == []


# ── 같은 run 에서 여러 번 잰 체온 · 여러 번 먹인 약 (최종 리뷰 F1) ──────
# run 의 중복 막기는 "대상 + 날짜" 로 센다. 체온 · 먹인 약은 한 말에서 여러 번 나온다 — 값과
# 시각이 다르면 다른 관찰이다. 두 번째를 막으면 모델이 첫 체온을 update 로 덮어쓴다
_TWO_READINGS = "아침 8시에 38.2도, 밤 9시에 39.1도였어"


async def test_같은_run_에서_체온_두_번은_두_행(context: AgentContext) -> None:
    base = {"raw_text": _TWO_READINGS, "observed_on": "오늘", "symptom": ["발열"]}
    llm = FakeLLM(
        _tools(
            _call(
                "c1",
                "create_observation_health",
                {**base, "temperature": 38.2, "measured_at": "아침 8시"},
            ),
            _call(
                "c2",
                "create_observation_health",
                {**base, "temperature": 39.1, "measured_at": "밤 9시"},
            ),
        ),
        _done(),
    )

    result = await run(_TWO_READINGS, context, client=llm)

    assert [call.success for call in result.calls] == [True, True]
    rows = await _rows(context)
    assert sorted(row.fields["temperature"] for row in rows) == [Decimal("38.2"), Decimal("39.1")]


async def test_같은_run_에서_약_두_번은_두_행(context: AgentContext) -> None:
    said = "아침 8시랑 밤 9시에 해열제 먹였어"
    base = {"raw_text": said, "observed_on": "오늘", "medication_given": "해열제"}
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_health", {**base, "given_time": "아침 8시"}),
            _call("c2", "create_observation_health", {**base, "given_time": "밤 9시"}),
        ),
        _done(),
    )

    result = await run(said, context, client=llm)

    assert [call.success for call in result.calls] == [True, True]
    rows = await _rows(context)
    assert len(rows) == 2
    assert {row.fields["observed_time"].hour for row in rows} == {8, 21}


async def test_완전히_같은_호출_두_번은_여전히_한_행(context: AgentContext) -> None:
    arguments = {**_FEVER_NIGHT}
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_health", arguments),
            _call("c2", "create_observation_health", arguments),
        ),
        _done(),
    )

    result = await run(arguments["raw_text"], context, client=llm)

    assert [call.success for call in result.calls] == [True, False]
    assert len(await _rows(context)) == 1


# ── 조회 결과에서 증상 행과 약 행을 가른다 (리뷰 1차) ──────────────
async def test_조회_결과에서_증상_행과_약_행이_구분된다(context: AgentContext) -> None:
    created = await _create(context, **_FEVER_AND_MEDICINE)

    result = await execute_tool("query_observation_health", {"date_from": "오늘"}, context)

    kinds = {record["id"]: record["kind"] for record in result.data["records"]}
    assert kinds == {created.data["id"]: "symptom", created.data["medication_id"]: "medication"}


async def test_다른_도메인의_조회_결과_모양은_그대로다(context: AgentContext) -> None:
    await execute_tool(
        "create_observation_food",
        {"raw_text": "사과 먹었어", "observed_on": "오늘", "subject": "사과"},
        context,
    )

    result = await execute_tool("query_observation_food", {"date_from": "오늘"}, context)

    [record] = result.data["records"]
    assert set(record) == {"id", "created_at", "raw_text"}


async def test_증상_행에_약을_넣으려_하면_약_행의_id_로_고치라고_안내한다(
    context: AgentContext,
) -> None:
    # 증상과 약을 한 번에 저장한 두 행은 raw_text 가 같다. 증상 행을 골랐으면 약 행으로 돌린다 —
    # "새로 만든다" 만 말하면 같은 투약이 두 번 남는다
    created = await _create(context, **_FEVER_AND_MEDICINE)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "medication_given": "해열제"},
        said(context, "해열제였어"),
    )

    assert result.success is False
    assert result.error is not None
    message = result.error["message"]
    assert "kind" in message and "medication" in message
    assert "새로 먹인 약일 때만" in message


# ── 체온도 보호자가 한 말과 대조한다 (#551) ───────────────────────
# LLM 이 바꾼 숫자는 열 타임라인과 3개월 미만 응급 안내까지 끌고 간다


@pytest.mark.parametrize(
    ("utterance", "temperature"),
    [
        ("열이 나서 재 보니 38.5도", 38.5),
        ("열이 38도 5부까지 올랐어", 38.5),
        ("열이 38도였어", 38.0),
        ("열이 39.0도", 39),
    ],
)
async def test_발화에_있는_체온은_저장한다(context: AgentContext, utterance, temperature) -> None:
    arguments = {"raw_text": utterance, "observed_on": "오늘", "symptom": ["발열"]}
    llm = FakeLLM(
        _tools(_call("c1", "create_observation_health", {**arguments, "temperature": temperature})),
        _done(),
    )

    result = await run(utterance, context, client=llm)

    assert [call.success for call in result.calls] == [True]
    [row] = await _rows(context)
    assert row.fields["temperature"] == Decimal(str(temperature))


@pytest.mark.parametrize(
    ("utterance", "temperature"),
    [
        ("열이 38.9도 같아", 38.5),  # 반올림 · 바꿔 쓰기
        ("열이 101도야", 38.3),  # 화씨를 섭씨로 환산
        ("열이 펄펄 끓어", 39.0),  # 숫자를 말하지 않았다
    ],
)
async def test_발화에_없는_체온은_저장하지_않는다(
    context: AgentContext, utterance, temperature
) -> None:
    # 모델이 raw_text 를 같이 바꿔 써도 이번 발화가 기준이다
    arguments = {
        "raw_text": f"열이 {temperature}도",
        "observed_on": "오늘",
        "symptom": ["발열"],
        "temperature": temperature,
    }
    llm = FakeLLM(_tools(_call("c1", "create_observation_health", arguments)), _done())

    result = await run(utterance, context, client=llm)

    [call] = result.calls
    assert call.success is False
    assert call.result["error"]["code"] == ErrorCode.INVALID_ARGS
    assert str(temperature) not in call.result["error"]["message"]  # 로그로 흘러가는 글이다
    assert await _rows(context) == []


async def test_수정으로_바꾼_체온이_발화에_없으면_고치지_않는다(context: AgentContext) -> None:
    created = await _create(context, **_FEVER_NIGHT)

    result = await execute_tool(
        "update_observation_health",
        {"observation_id": created.data["id"], "temperature": 39.5},
        said(context, "아니 39도였어"),
    )

    assert result.success is False
    [row] = await _rows(context)
    assert row.fields["temperature"] == Decimal("38.5")


@pytest.mark.parametrize("change", [{"medication_given": "부루펜"}, {"temperature": 38.5}])
async def test_발화를_모르는_수정은_약_이름_체온을_고치지_않는다(
    context: AgentContext, change
) -> None:
    # run 을 거치지 않은 직접 호출 — 대조할 글이 없으면 통과시키지 않는다
    created = await _create(context, **_FEVER_NIGHT)
    medicine = await _create(
        context, raw_text="해열제 먹였어", observed_on="오늘", medication_given="해열제"
    )
    target = medicine if "medication_given" in change else created

    result = await execute_tool(
        "update_observation_health", {"observation_id": target.data["id"], **change}, context
    )

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS


# ── 같은 run 에서 증상 · 약을 따로 센다 (#551) ─────────────────────
# 증상과 약을 한 번에 세면 "발열 38.5" 뒤의 "발열 38.5 + 해열제" 가 통과해 증상 행이 둘이 된다


async def test_증상을_저장한_뒤_같은_증상과_약을_다시_부르면_막는다(context: AgentContext) -> None:
    utterance = "열이 나서 38.5도였고 해열제 먹였어"
    base = {"raw_text": utterance, "observed_on": "오늘", "symptom": ["발열"], "temperature": 38.5}
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_health", base),
            _call("c2", "create_observation_health", {**base, "medication_given": "해열제"}),
        ),
        _done(),
    )

    result = await run(utterance, context, client=llm)

    assert [call.success for call in result.calls] == [True, False]
    message = result.calls[1].result["error"]["message"]
    assert "남은 쪽만" in message and "해열제" not in message
    rows = await _rows(context)
    assert [row.fields["symptom"] for row in rows] == [["발열"]]


async def test_막힌_뒤_약만_다시_부르면_약_행이_생긴다(context: AgentContext) -> None:
    utterance = "열이 나서 38.5도였고 해열제 먹였어"
    base = {"raw_text": utterance, "observed_on": "오늘", "symptom": ["발열"], "temperature": 38.5}
    medicine = {"raw_text": utterance, "observed_on": "오늘", "medication_given": "해열제"}
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_health", base),
            _call("c2", "create_observation_health", {**base, "medication_given": "해열제"}),
        ),
        _tools(_call("c3", "create_observation_health", medicine)),
        _done(),
    )

    result = await run(utterance, context, client=llm)

    assert [call.success for call in result.calls] == [True, False, True]
    rows = await _rows(context)
    assert sorted(bool(row.fields["symptom"]) for row in rows) == [False, True]


async def test_약을_저장한_뒤_같은_약을_다시_부르면_막는다(context: AgentContext) -> None:
    utterance = "해열제 먹였어"
    medicine = {"raw_text": utterance, "observed_on": "오늘", "medication_given": "해열제"}
    llm = FakeLLM(
        _tools(
            _call("c1", "create_observation_health", medicine),
            _call("c2", "create_observation_health", medicine),
        ),
        _done(),
    )

    result = await run(utterance, context, client=llm)

    assert [call.success for call in result.calls] == [True, False]
    assert len(await _rows(context)) == 1


def test_프롬프트의_체온_예시는_증상과_같이_있다(context: AgentContext) -> None:
    # 증상 없이 체온만 넣으면 스키마가 거절한다 — 예시가 그 길로 이끌면 모델이 발열을 지어낸다
    prompt = build_system_prompt(context)
    assert 'symptom=["열"], temperature=38.5' in prompt
    assert "증상을 지어내지 않고" in prompt
