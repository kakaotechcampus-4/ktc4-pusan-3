# LLM 호출 공통 클라이언트. Supervisor, 도메인 Agent 함께 사용

import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from app.agents.common.config import AgentSettings, get_agent_settings

logger = logging.getLogger(__name__)

_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high"})


class LLMError(Exception):
    """Agent 레이어가 다루는 LLM 호출 실패의 공통 타입."""


class LLMConfigError(LLMError):
    """MEMORY_API_KEY / MEMORY_BASE_URL / MEMORY_MODEL / MEMORY_REASONING_EFFORT 가
    비었거나 잘못됨."""


class LLMAuthError(LLMError):
    """인증 실패. API_KEY 를 확인한다."""


class LLMBadRequestError(LLMError):
    """요청이 거절됨. 지원하지 않는 파라미터거나 tool schema 가 잘못됨."""


class LLMRateLimitError(LLMError):
    """rate limit 초과. 호출 측에서 backoff 한다."""


class LLMUnavailableError(LLMError):
    """네트워크·타임아웃 등 일시적 장애."""


@dataclass(frozen=True)
class LLMResponse:
    message: Any  # openai ChatCompletionMessage — content / tool_calls 접근용
    usage: dict[str, int]  # prompt / completion / cached token 수
    latency_ms: int


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}

    # cached token 은 SDK 버전에 따라 위치가 달라서 방어적으로 꺼내기
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details else None
    result = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
    }
    if cached is not None:
        result["cached_prompt_tokens"] = cached
    return result


class LLMClient:
    """chat.completions 한 번의 왕복만 책임진다. tool 실행과 루프는 Agent 쪽 몫이다."""

    def __init__(self, settings: AgentSettings | None = None) -> None:
        s = settings or get_agent_settings()

        if not s.MEMORY_API_KEY or not s.MEMORY_BASE_URL:
            raise LLMConfigError(
                "MEMORY_API_KEY / MEMORY_BASE_URL 이 비어 있다. apps/api/.env 를 확인한다."
            )
        if not s.MEMORY_MODEL:
            raise LLMConfigError("MEMORY_MODEL 이 비어 있다. apps/api/.env 를 확인한다.")
        if s.MEMORY_REASONING_EFFORT not in _REASONING_EFFORTS:
            allowed = ", ".join(sorted(_REASONING_EFFORTS))
            raise LLMConfigError(f"MEMORY_REASONING_EFFORT 는 {allowed} 중 하나여야 한다.")

        self._model = s.MEMORY_MODEL
        self._reasoning_effort = s.MEMORY_REASONING_EFFORT
        self._client = AsyncOpenAI(
            base_url=s.MEMORY_BASE_URL,
            api_key=s.MEMORY_API_KEY,
            timeout=s.LLM_TIMEOUT_S,
            max_retries=s.LLM_MAX_RETRIES,
        )

    @property
    def model(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        max_completion_tokens: int | None = None,
    ) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "reasoning_effort": self._reasoning_effort,
        }
        # tools가 있는 경우에만 tool_choice 보냄
        if tools:
            # 지금은 reasoning_effort=none 이어야 tool을 쓸 수 있음
            if self._reasoning_effort != "none":
                raise LLMConfigError(
                    "tool을 쓰려면 MEMORY_REASONING_EFFORT=none 이어야 한다 "
                    f"(현재 {self._reasoning_effort}). apps/api/.env 를 확인한다."
                )
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens

        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**params)
        except AuthenticationError as exc:
            raise LLMAuthError("LLM 인증 실패. API_KEY 를 확인한다.") from exc
        except BadRequestError as exc:
            # 서버가 돌려준 사유만 담음. 요청 본문(발화 원문)은 싣지 않는다.
            raise LLMBadRequestError(f"LLM 요청 거절: {exc}") from exc
        except RateLimitError as exc:
            raise LLMRateLimitError("LLM rate limit 초과.") from exc
        except APIConnectionError as exc:
            raise LLMUnavailableError("LLM 연결 실패 또는 타임아웃.") from exc
        except APIError as exc:
            raise LLMError(f"LLM 호출 실패: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        message = response.choices[0].message
        usage = _usage_dict(response.usage)
        self._log_call(message, usage, latency_ms)
        return LLMResponse(message=message, usage=usage, latency_ms=latency_ms)

    def _log_call(self, message: Any, usage: dict[str, int], latency_ms: int) -> None:
        tool_calls = getattr(message, "tool_calls", None) or []

        # 발화 원문·프롬프트·응답 본문은 로그에 남기지 않는다
        logger.info(
            "llm call model=%s prompt_tokens=%s completion_tokens=%s "
            "tool_calls=%d tools=%s latency_ms=%d",
            self._model,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            len(tool_calls),
            [call.function.name for call in tool_calls],
            latency_ms,
        )
