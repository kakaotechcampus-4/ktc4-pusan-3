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

from app.agents.common.config import (
    AgentConfigError,
    AgentRole,
    AgentSettings,
    get_agent_settings,
)

logger = logging.getLogger(__name__)

_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high"})


class LLMError(Exception):
    """Agent 레이어가 다루는 LLM 호출 실패의 공통 타입."""


class LLMConfigError(LLMError):
    """역할의 API_KEY / BASE_URL / MODEL / REASONING_EFFORT 가 비었거나 잘못됨.
    메시지에는 실제로 읽은 .env 키를 적는다 — 대체됐으면 MEMORY_* 이름이 나온다."""


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

    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(prompt_details, "cached_tokens", None) if prompt_details else None
    completion_details = getattr(usage, "completion_tokens_details", None)
    reasoning = (
        getattr(completion_details, "reasoning_tokens", None) if completion_details else None
    )
    result = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
    }
    if cached is not None:
        result["cached_prompt_tokens"] = cached
    if reasoning is not None:
        # completion_tokens에 이미 포함된 값
        result["reasoning_tokens"] = reasoning
    return result


class LLMClient:
    """chat.completions 한 번의 왕복만 책임진다. tool 실행과 루프는 Agent 쪽 몫이다.

    role 마다 키·주소·모델·effort 를 따로 둘 수 있다 (AgentSettings.profile).
    역할의 키가 비어 있으면 MEMORY_* 를 쓴다. 기본은 supervisor.
    Memory Agent 는 role="memory" 를 명시한다 — Supervisor 모델을 바꿔 끼워도 Memory 는 그대로다.
    """

    def __init__(
        self, settings: AgentSettings | None = None, *, role: AgentRole = "supervisor"
    ) -> None:
        s = settings or get_agent_settings()
        try:
            profile = s.profile(role)
        except AgentConfigError as exc:
            raise LLMConfigError(f"{exc} apps/api/.env 를 확인한다.") from exc

        if not profile.api_key or not profile.base_url:
            keys = f"{profile.key('api_key')} / {profile.key('base_url')}"
            raise LLMConfigError(f"{keys} 가 비어 있다. apps/api/.env 를 확인한다.")
        if not profile.model:
            raise LLMConfigError(f"{profile.key('model')} 가 비어 있다. apps/api/.env 를 확인한다.")
        if profile.reasoning_effort not in _REASONING_EFFORTS:
            allowed = ", ".join(sorted(_REASONING_EFFORTS))
            raise LLMConfigError(
                f"{profile.key('reasoning_effort')} 는 {allowed} 중 하나여야 한다."
            )

        self._profile = profile
        self._client = AsyncOpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key,
            timeout=s.LLM_TIMEOUT_S,
            max_retries=s.LLM_MAX_RETRIES,
        )

    @property
    def model(self) -> str:
        return self._profile.model

    @property
    def role(self) -> AgentRole:
        return self._profile.role

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        max_completion_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """tool_choice 는 "auto"/"required" 같은 문자열이나
        {"type": "function", "function": {"name": ...}} 같은 강제 호출 dict를 그대로 보낸다.
        response_format 은 {"type": "json_schema", ...} 를 그대로 보낸다.
        """
        params: dict[str, Any] = {
            "model": self._profile.model,
            "messages": messages,
            "reasoning_effort": self._profile.reasoning_effort,
        }
        # tools가 있는 경우에만 tool_choice 보냄
        if tools:
            # 지금은 reasoning_effort=none 이어야 tool을 쓸 수 있음 (역할별 설정에도 같은 규칙)
            if self._profile.reasoning_effort != "none":
                raise LLMConfigError(
                    f"tool을 쓰려면 {self._profile.key('reasoning_effort')}=none 이어야 한다 "
                    f"(현재 {self._profile.reasoning_effort}). apps/api/.env 를 확인한다."
                )
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens
        if response_format is not None:
            params["response_format"] = response_format

        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**params)
        except AuthenticationError as exc:
            raise LLMAuthError("LLM 인증 실패. API_KEY 를 확인한다.") from exc
        except BadRequestError as exc:
            # 서버가 돌려준 사유만 담음. 요청 본문(발화 원문)은 싣지 않음
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
            "llm call role=%s model=%s prompt_tokens=%s completion_tokens=%s "
            "reasoning_tokens=%s tool_calls=%d tools=%s latency_ms=%d",
            self._profile.role,
            self._profile.model,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("reasoning_tokens"),
            len(tool_calls),
            [call.function.name for call in tool_calls],
            latency_ms,
        )
