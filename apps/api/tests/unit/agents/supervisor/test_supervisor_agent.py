"""Supervisor run() 검증. 가짜 LLM 으로 돌리므로 실제 API 를 부르지 않는다.

Supervisor 가 실패해도 입력 처리는 계속돼야 한다 — run() 은 예외를 올리지 않고 error 코드로
돌려주고, **재시도하지 않는다**. 강등 여부는 pipeline 이 정한다 (supervisor_plan S5).
"""

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.common.llm_client import LLMBadRequestError, LLMResponse, LLMUnavailableError
from app.agents.supervisor.agent import (
    _FORCE_ROUTE,
    BAD_JSON,
    LLM_UNAVAILABLE,
    NO_TOOL_CALL,
    SCHEMA,
    _attempts,
    run,
)
from app.agents.supervisor.schemas import NOT_SUBSTRING

RAW = "오늘 사과를 먹었어. 저녁에는 뭘 먹이면 좋을까?"
_GOOD = {
    "segments": [
        {"text": "오늘 사과를 먹었어", "kind": "record", "work": "observe"},
        {
            "text": "저녁에는 뭘 먹이면 좋을까?",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
    ]
}


class FakeLLM:
    """정해둔 응답을 돌려주고, 무엇을 요청했는지 기록한다."""

    def __init__(self, *responses: Any) -> None:
        self._queue = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **params: Any) -> LLMResponse:
        self.calls.append(params)
        item = self._queue.pop(0) if self._queue else _route(_GOOD)
        if isinstance(item, Exception):
            raise item
        return item


def _response(message: Any, usage: dict[str, int] | None = None) -> LLMResponse:
    return LLMResponse(message=message, usage=usage or {"prompt_tokens": 100}, latency_ms=1)


def _route(arguments: dict[str, Any] | str, name: str = "route") -> LLMResponse:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name=name, arguments=raw))
    return _response(SimpleNamespace(content=None, tool_calls=[call]))


def _reply(content: str) -> LLMResponse:
    return _response(SimpleNamespace(content=content, tool_calls=None))


# ── 정상 ────────────────────────────────────────────────────────
async def test_조각을_그대로_돌려준다() -> None:
    result = await run(RAW, client=FakeLLM(_route(_GOOD)))

    assert result.error is None and result.ok
    assert result.output is not None
    kinds = [segment.kind for segment in result.output.segments]
    assert kinds == ["record", "request"]
    assert result.output.segments[1].food_task == "meal_recommendation"
    assert result.model_calls == 1
    assert result.usage == {"prompt_tokens": 100}


async def test_route_를_강제로_부르게_한다() -> None:
    llm = FakeLLM(_route(_GOOD))
    await run(RAW, client=llm)

    params = llm.calls[0]
    assert params["tool_choice"] == {"type": "function", "function": {"name": "route"}}
    assert [spec["function"]["name"] for spec in params["tools"]] == ["route"]
    # 입력은 발화 원문 하나뿐이다 (S10)
    assert params["messages"][-1] == {"role": "user", "content": RAW}


# ── 검증 실패 — 전부 error 로 돌아온다 ──────────────────────────
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_route('{"segments": [{"text":'), BAD_JSON),  # 잘린 JSON
        (_route("[1, 2]"), BAD_JSON),  # 객체가 아니다
        (_route({"segments": []}), SCHEMA),  # 조각 0개
        (_route({"segments": [{"text": "오늘 사과를 먹었어", "kind": "record"}]}), SCHEMA),
        (
            _route(
                {"segments": [{"text": "사과를 먹은 기록", "kind": "record", "work": "observe"}]}
            ),
            NOT_SUBSTRING,
        ),  # 의역
        (_reply("무엇을 도와드릴까요?"), NO_TOOL_CALL),
        (_route(_GOOD, name="create_observation_food"), NO_TOOL_CALL),  # 다른 tool 을 불렀다
    ],
    ids=[
        "깨진JSON",
        "객체아님",
        "조각0개",
        "work없음",
        "의역",
        "tool미호출",
        "다른tool",
    ],
)
async def test_검증에_실패하면_error_로_돌려준다(response: Any, code: str) -> None:
    result = await run(RAW, client=FakeLLM(response))

    assert result.output is None
    assert result.error == code
    assert result.model_calls == 1  # 재시도하지 않는다 (S5)


async def test_조각_하나가_깨져도_나머지로_간다() -> None:
    """라이브에서 5조각 중 1조각이 깨져 분리 전체가 버려지는 일이 반복됐다.

    매번 다른 조각이 다르게 깨진다(없는 enum 값·빠진 필수 필드). 하나 때문에 전부 버리면
    같은 발화의 멀쩡한 요청도 도메인 Agent 에 닿지 못한다. 버린 조각의 원문은
    Memory 가 raw_text 로 그대로 본다 (S2).
    """
    mixed = {
        "segments": [
            {"text": "오늘 사과를 먹었어", "kind": "record", "work": "observe"},
            {"text": "저녁에는 뭘 먹이면 좋을까?", "kind": "기록형"},  # 없는 kind
        ]
    }
    result = await run(RAW, client=FakeLLM(_route(mixed)))

    assert result.error is None
    assert result.output is not None
    assert [segment.text for segment in result.output.segments] == ["오늘 사과를 먹었어"]
    assert result.dropped_segments == 1


async def test_전부_깨지면_출력을_내지_않는다() -> None:
    broken = {"segments": [{"text": "오늘 사과를 먹었어", "kind": "기록형"}]}
    result = await run(RAW, client=FakeLLM(_route(broken)))

    assert result.output is None
    assert result.error == SCHEMA
    assert result.dropped_segments == 0  # 쓸 수 있는 게 없으면 버린 게 아니라 실패다


async def test_짝이_아닌_필드는_지우고_출력을_살린다() -> None:
    """라이브에서 가장 잦았던 실패다 — 라벨은 맞는데 안 읽는 필드가 하나 더 붙어 왔다.

    29회 중 6회가 이것 때문에 출력 전체를 잃고 강등됐다. 강등되면 요청 조각이
    도메인 Agent 로 가지 못한다 (RC20 — 영양소 분석이 아예 안 불렸다).
    """
    over_filled = {
        "segments": [
            {
                "text": "저녁에는 뭘 먹이면 좋을까?",
                "kind": "request",
                "agent": "food",
                "food_task": "meal_recommendation",
                "work": "lookup_edit",  # request 조각에는 없는 짝
            }
        ]
    }
    result = await run(RAW, client=FakeLLM(_route(over_filled)))

    assert result.error is None
    assert result.output is not None
    segment = result.output.segments[0]
    assert segment.work is None  # 지워졌다
    assert (segment.agent, segment.food_task) == ("food", "meal_recommendation")


async def test_어디가_걸렸는지_값_없이_알려준다() -> None:
    # 스키마 실패는 필드 경로와 사유만. 값에는 발화 원문이 들어 있다
    bad = {"segments": [{"text": "오늘 사과를 먹었어", "kind": "record"}]}
    result = await run(RAW, client=FakeLLM(_route(bad)))

    assert result.error == SCHEMA
    assert result.detail is not None
    assert "work" in result.detail
    assert "사과" not in result.detail


async def test_의역한_조각만_버리고_나머지로_간다() -> None:
    # 의역은 출처 단서를 지운다. 다만 그 조각만 버리면 되고, 나머지 분리까지 버릴 이유는 없다
    bad = {
        "segments": [
            {"text": "오늘 사과를 먹었어", "kind": "record", "work": "observe"},
            {"text": "저녁 메뉴 추천해줘", "kind": "request", "agent": "activity"},  # 의역
        ]
    }
    result = await run(RAW, client=FakeLLM(_route(bad)))

    assert result.error is None
    assert result.output is not None
    assert [segment.text for segment in result.output.segments] == ["오늘 사과를 먹었어"]
    assert result.dropped_segments == 1


async def test_전부_의역이면_조각_번호를_알려준다() -> None:
    bad = {"segments": [{"text": "저녁 메뉴 추천해줘", "kind": "request", "agent": "activity"}]}
    result = await run(RAW, client=FakeLLM(_route(bad)))

    assert result.error == NOT_SUBSTRING
    assert result.detail is not None and "1" in result.detail
    assert "메뉴" not in result.detail
    assert result.rejected is None  # 기본값 — 조각 원문을 들고 나가지 않는다


async def test_keep_rejected_를_켜면_버려진_조각을_볼_수_있다() -> None:
    # 합성 케이스로 프롬프트를 고칠 때만 켠다 (라이브 분기 확인)
    bad = {"segments": [{"text": "지어낸 문장", "kind": "unclear"}]}
    result = await run(RAW, client=FakeLLM(_route(bad)), keep_rejected=True)

    assert result.output is None
    assert result.rejected is not None
    assert result.rejected.segments[0].text == "지어낸 문장"


async def test_LLM_오류는_예외를_올리지_않는다() -> None:
    result = await run(RAW, client=FakeLLM(LLMUnavailableError("네트워크")))

    assert result.error == LLM_UNAVAILABLE
    assert result.output is None


# ── tool_choice 지원 여부 ───────────────────────────────────────
def test_기본은_strict_를_쓰지_않는다() -> None:
    """strict 는 스키마 실패를 막지만 라벨을 망친다 — 실측으로 끄기로 했다.

    선택 필드가 null 허용 타입이라 모델이 고르는 대신 null 을 채운다.
    RC07 놀이 추천이 켜면 unclear ×3, 끄면 request/activity ×3 이었다.
    """
    assert all("strict" not in spec["function"] for spec, _ in _attempts(strict=False))


def test_켜면_strict_를_먼저_보낸다() -> None:
    spec, choice = _attempts(strict=True)[0]
    assert spec["function"]["strict"] is True
    assert choice == _FORCE_ROUTE
    item = spec["function"]["parameters"]["properties"]["segments"]["items"]
    assert set(item["required"]) == set(item["properties"])  # strict 는 전부 required 여야 받는다


async def test_강제_호출을_거절하면_다음_값으로_물러선다() -> None:
    # provider 가 dict tool_choice 를 모를 수 있다. 첫 스모크에서 확인하고 되는 값으로 고정한다
    llm = FakeLLM(LLMBadRequestError("tool_choice"), _route(_GOOD))
    result = await run(RAW, client=llm)

    assert result.ok
    assert result.model_calls == 2
    assert llm.calls[1]["tool_choice"] == "required"


async def test_전부_거절되면_LLM_오류로_끝낸다() -> None:
    llm = FakeLLM(*[LLMBadRequestError("tool_choice")] * 3)
    result = await run(RAW, client=llm)

    assert result.error == LLM_UNAVAILABLE
    assert result.model_calls == 3  # tool_choice 3가지


# ── 로그 ────────────────────────────────────────────────────────
async def test_로그에_조각_원문이_없다(caplog: pytest.LogCaptureFixture) -> None:
    # 루트 §2 — 로그에 원문 대신 개수·라벨만
    caplog.set_level(logging.INFO, logger="app.agents.supervisor.agent")
    await run(RAW, client=FakeLLM(_route(_GOOD)))

    assert "사과" not in caplog.text
    assert "먹이면" not in caplog.text
    assert "segments=2" in caplog.text


async def test_실패도_코드만_남긴다(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.agents.supervisor.agent")
    await run(RAW, client=FakeLLM(_reply("무엇을 도와드릴까요?")))

    assert f"error={NO_TOOL_CALL}" in caplog.text
    assert "사과" not in caplog.text
