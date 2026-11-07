"""실패 종류 계통 (#768).

예외 종류 하나가 `failed.reason` 과 화면 문구를 정한다. 여기서는 그 약속만 본다.
    - 클래스마다 이유가 정해져 있고, 계통 밖 예외는 internal_error 다
    - OpenAI 의 시간 초과 · 연결 실패 · rate limit · 인증이 서로 다른 이유가 된다
    - 옮긴 클래스를 원래 모듈에서도 같은 객체로 import 한다
    - 모든 이유에 문구가 있고, 글자까지 같다
"""

from types import SimpleNamespace
from typing import Any, get_args

import httpx
import openai
import pytest

from app.agents.common import errors, guardrail, llm_client, ports
from app.agents.common.config import AgentSettings
from app.agents.common.failure import catalog_keys, failure_message
from app.agents.food.store import ports as food_ports
from app.agents.growth.tools import safety as growth_safety
from app.core.book_raw import UpstreamUnavailable as BookUpstreamUnavailable
from app.core.weather_raw import UpstreamUnavailable as WeatherUpstreamUnavailable

REQUEST = httpx.Request("POST", "https://llm.example.invalid/v1/chat/completions")


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (errors.AgentFailure(), "internal_error"),
        (errors.LLMError(), "llm_unavailable"),
        (errors.LLMUnavailableError(), "llm_unavailable"),
        (errors.LLMTimeoutError(), "timeout"),
        (errors.LLMRateLimitError(), "llm_rate_limited"),
        (errors.LLMAuthError(), "llm_misconfigured"),
        (errors.LLMConfigError(), "llm_misconfigured"),
        (errors.LLMBadRequestError(), "llm_misconfigured"),
        (errors.LookupUnavailable(), "lookup_unavailable"),
        (errors.SafetyLookupError(), "lookup_unavailable"),
        (errors.MenuSourceError(), "lookup_unavailable"),
        (errors.SafetyRulesUnavailable(), "lookup_unavailable"),
        (errors.GuardrailUnavailableError(), "guard_unavailable"),
    ],
)
def test_클래스마다_실패_이유가_정해져_있다(exc: BaseException, reason: str) -> None:
    assert errors.failure_reason(exc) == reason


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("부서졌다"),
        ValueError("값"),
        TimeoutError(),  # 시간 초과는 pipeline 이 TimeoutError 로 따로 센다. 계통 밖이다
        WeatherUpstreamUnavailable("날씨"),  # core 의 것은 계통 밖이다 (레이어 규칙)
        BookUpstreamUnavailable("도서"),
    ],
)
def test_계통_밖_예외는_internal_error_다(exc: BaseException) -> None:
    assert errors.failure_reason(exc) == "internal_error"


def test_LLM_시간_초과는_내장_TimeoutError_가_아니다() -> None:
    # 러너의 `except TimeoutError` 가 60초 안전망으로, 도메인 단계가 20초 마감으로 오인한다
    assert not issubclass(errors.LLMTimeoutError, TimeoutError)
    assert issubclass(errors.LLMTimeoutError, errors.LLMError)


def test_기존_except_LLMError_가_그대로_잡는다() -> None:
    for cls in (
        errors.LLMTimeoutError,
        errors.LLMUnavailableError,
        errors.LLMRateLimitError,
        errors.LLMAuthError,
        errors.LLMConfigError,
        errors.LLMBadRequestError,
    ):
        assert issubclass(cls, errors.LLMError)
    assert issubclass(errors.LLMError, errors.AgentFailure)


def test_옮긴_클래스를_원래_모듈에서도_같은_객체로_import_한다() -> None:
    assert llm_client.LLMError is errors.LLMError
    assert llm_client.LLMTimeoutError is errors.LLMTimeoutError
    assert llm_client.LLMUnavailableError is errors.LLMUnavailableError
    assert llm_client.LLMRateLimitError is errors.LLMRateLimitError
    assert llm_client.LLMAuthError is errors.LLMAuthError
    assert llm_client.LLMConfigError is errors.LLMConfigError
    assert llm_client.LLMBadRequestError is errors.LLMBadRequestError
    assert ports.SafetyLookupError is errors.SafetyLookupError
    assert food_ports.SafetyLookupError is errors.SafetyLookupError
    assert food_ports.MenuSourceError is errors.MenuSourceError
    assert growth_safety.SafetyRulesUnavailable is errors.SafetyRulesUnavailable
    assert guardrail.GuardrailUnavailableError is errors.GuardrailUnavailableError


# ── LLMClient 가 OpenAI 예외를 갈라 낸다 ─────────────────────────────


class _Raising:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def create(self, **_params: Any) -> Any:
        raise self._exc


def _client(exc: Exception) -> llm_client.LLMClient:
    settings = AgentSettings(
        _env_file=None,
        MEMORY_API_KEY="test-key",
        MEMORY_BASE_URL="https://memory-gw.example.invalid/v1",
        MEMORY_MODEL="memory-model",
        MEMORY_REASONING_EFFORT="none",
    )
    client = llm_client.LLMClient(settings, role="memory")
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=_Raising(exc)))
    return client


def _status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    return cls("실패", response=httpx.Response(status, request=REQUEST), body=None)


@pytest.mark.parametrize(
    ("raised", "expected", "reason"),
    [
        (openai.APITimeoutError(request=REQUEST), errors.LLMTimeoutError, "timeout"),
        (
            openai.APIConnectionError(request=REQUEST),
            errors.LLMUnavailableError,
            "llm_unavailable",
        ),
        (
            _status_error(openai.RateLimitError, 429),
            errors.LLMRateLimitError,
            "llm_rate_limited",
        ),
        (
            _status_error(openai.AuthenticationError, 401),
            errors.LLMAuthError,
            "llm_misconfigured",
        ),
        (
            _status_error(openai.BadRequestError, 400),
            errors.LLMBadRequestError,
            "llm_misconfigured",
        ),
        (
            _status_error(openai.InternalServerError, 500),
            errors.LLMError,
            "llm_unavailable",
        ),
    ],
)
async def test_OpenAI_예외가_서로_다른_이유가_된다(
    raised: Exception, expected: type[Exception], reason: str
) -> None:
    with pytest.raises(errors.LLMError) as caught:
        await _client(raised).chat([{"role": "user", "content": "hi"}])

    assert type(caught.value) is expected
    assert errors.failure_reason(caught.value) == reason


async def test_시간_초과가_연결_실패로_뭉치지_않는다() -> None:
    # openai 의 APITimeoutError 는 APIConnectionError 의 하위다 — 잡는 순서가 거꾸로면 여기서 깨진다
    assert issubclass(openai.APITimeoutError, openai.APIConnectionError)
    with pytest.raises(errors.LLMTimeoutError):
        await _client(openai.APITimeoutError(request=REQUEST)).chat([])


# ── 문구 카탈로그 ──────────────────────────────────────────────────


def test_모든_이유에_문구가_있고_남는_키가_없다() -> None:
    assert catalog_keys() == frozenset(get_args(errors.FailReason))
    assert tuple(get_args(errors.FailReason)) == errors.FAIL_REASONS


def test_이유마다_문구가_글자까지_같다() -> None:
    assert {reason: failure_message(reason) for reason in get_args(errors.FailReason)} == {
        "timeout": "시간 안에 답을 만들지 못했어요. 잠시 뒤 다시 보내 주세요.",
        "llm_unavailable": "지금은 답을 만들 수 없어요. 잠시 뒤 다시 보내 주세요.",
        "llm_rate_limited": "지금 요청이 많아요. 잠시 뒤 다시 보내 주세요.",
        "llm_misconfigured": (
            "서비스 쪽 문제로 지금은 처리할 수 없어요. 다시 보내도 같을 수 있어요."
        ),
        "guard_unavailable": "안전 확인을 하지 못했어요. 잠시 뒤 다시 보내 주세요.",
        "lookup_unavailable": "필요한 정보를 불러오지 못했어요. 잠시 뒤 다시 보내 주세요.",
        "unparsable": "말씀을 정리하지 못했어요. 조금 바꿔서 다시 적어 주세요.",
        "internal_error": "처리하다 문제가 생겼어요. 다시 보내 주세요.",
    }


def test_모르는_이유는_internal_error_문구로_낸다() -> None:
    # 문구 없는 failed 를 내지 않는다
    assert failure_message("나중에_생길_값") == failure_message("internal_error")


def test_문구가_기록_여부를_다시_말하지_않는다() -> None:
    # failed 는 "저장 없음" 이 보장된 끝이다. 둘째 줄이 그 말을 한다 — 문구가 겹치면 모순이 생긴다
    for reason in get_args(errors.FailReason):
        assert "기록" not in failure_message(reason)
        assert "저장" not in failure_message(reason)
