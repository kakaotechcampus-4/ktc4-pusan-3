# LLM 호출 공통 클라이언트. Supervisor, 도메인 Agent 함께 사용

import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
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
from app.agents.common.prompt_fingerprint import is_registered, logged_fingerprint

# 실패 예외는 errors.py 에 있다. 원래 import 경로(`llm_client.LLMError` 등)를 지키려고
# 다시 내보낸다 (#768)
from app.agents.common.errors import (
    LLMAuthError as LLMAuthError,
)
from app.agents.common.errors import (
    LLMBadRequestError as LLMBadRequestError,
)
from app.agents.common.errors import (
    LLMConfigError as LLMConfigError,
)
from app.agents.common.errors import (
    LLMError as LLMError,
)
from app.agents.common.errors import (
    LLMRateLimitError as LLMRateLimitError,
)
from app.agents.common.errors import (
    LLMTimeoutError as LLMTimeoutError,
)
from app.agents.common.errors import (
    LLMUnavailableError as LLMUnavailableError,
)
from app.core import llm_usage

logger = logging.getLogger(__name__)

_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high"})


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
        self._provider = llm_usage.provider_of(profile.base_url)
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
        prompt_name: str | None = None,
    ) -> LLMResponse:
        """tool_choice 는 "auto"/"required" 같은 문자열이나
        {"type": "function", "function": {"name": ...}} 같은 강제 호출 dict를 그대로 보낸다.
        response_format 은 {"type": "json_schema", ...} 를 그대로 보낸다.
        prompt_name 은 로그에 달 프롬프트 이름(`register_prompt` 에 등록한 이름)이다.
        안 주면 역할 이름을 쓴다 —
        역할 하나가 프롬프트 하나인 곳(supervisor · memory · food · growth · health · activity)은
        그대로 맞고, 한 역할이 다른 프롬프트도 부르는 곳(activity 의 행사 일정)만 이름을 준다.
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
        except APITimeoutError as exc:
            # APIConnectionError 의 하위라 먼저 잡는다
            raise LLMTimeoutError("LLM 응답 시간 초과.") from exc
        except APIConnectionError as exc:
            raise LLMUnavailableError("LLM 연결 실패.") from exc
        except APIError as exc:
            raise LLMError(f"LLM 호출 실패: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = _usage_dict(response.usage)
        # 게이트웨이가 별칭을 실제 모델로 풀 수 있다 — 과금은 응답에 찍힌 모델 기준이다
        served = getattr(response, "model", None)
        model = served if isinstance(served, str) and served else self._profile.model
        # 🚨 이상한 응답을 거절하기 전에 기록한다 — 거절해도 토큰은 이미 과금됐다
        if usage:  # 셀 값이 없는 응답은 0 토큰 행을 만들지 않는다
            llm_usage.record(
                role=self._profile.role,
                provider=self._provider,
                model=model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                cached_prompt_tokens=usage.get("cached_prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                reasoning_tokens=usage.get("reasoning_tokens", 0),
                latency_ms=latency_ms,
            )
        # 콘텐츠 필터 등을 이유로 choices가 비거나 message가 null로 오는 provider의 경우
        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None)
        if message is None:
            raise LLMError("LLM 응답에 message가 없다.")
        self._log_call(message, usage, latency_ms, model, prompt_name)
        return LLMResponse(message=message, usage=usage, latency_ms=latency_ms)

    def _prompt_print(self, prompt_name: str | None) -> tuple[str, str]:
        """로그에 달 (프롬프트 이름, 지문 12자).

        모르는 이름이거나 지문을 못 만들면 대시다 — 호출은 막지 않는다.
        """
        name = prompt_name or self._profile.role
        if not is_registered(name):
            return "-", "-"
        try:
            return name, logged_fingerprint(name).short
        except Exception as exc:  # 로그용 값이 호출을 깨뜨리면 안 된다
            logger.warning("프롬프트 지문을 못 만들었다 name=%s error=%s", name, type(exc).__name__)
            return name, "-"

    def _log_call(
        self,
        message: Any,
        usage: dict[str, int],
        latency_ms: int,
        model: str,
        prompt_name: str | None = None,
    ) -> None:
        tool_calls = getattr(message, "tool_calls", None) or []
        attribution = llm_usage.current()
        name, sha = self._prompt_print(prompt_name)

        # 발화 원문·프롬프트·응답 본문은 로그에 남기지 않는다
        logger.info(
            "llm call role=%s model=%s run=%s source=%s prompt_name=%s prompt_sha256=%s "
            "prompt_tokens=%s "
            "cached_prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s "
            "tool_calls=%d tools=%s latency_ms=%d",
            self._profile.role,
            model,
            attribution.run_id if attribution else "-",
            attribution.source if attribution else "-",
            name,
            sha,
            usage.get("prompt_tokens"),
            usage.get("cached_prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("reasoning_tokens"),
            len(tool_calls),
            [call.function.name for call in tool_calls],
            latency_ms,
        )
