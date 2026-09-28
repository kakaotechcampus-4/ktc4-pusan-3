# Agent 레이어 전용 설정
# 같은 `apps/api/.env` 를 읽되 Agent 가 쓰는 키만 여기서 다룬다

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator

from app.core.agent_config import AGENT_ROLES, AgentLLMSettings, AgentRole

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

DEFAULT_REASONING_EFFORT = "none"

# LLM을 부르는 역할. 역할의 키가 비어 있으면 MEMORY_*를 사용
_PREFIX: dict[str, str] = {role.lower(): role for role in AGENT_ROLES}


class AgentConfigError(ValueError):
    """역할 설정이 서로 맞지 않는다. 예: SUPERVISOR_MODEL만 있고 SUPERVISOR_BASE_URL이 없다.
    LLMClient가 LLMConfigError로 바꿔 올린다."""


@dataclass(frozen=True)
class LLMProfile:
    """역할 하나가 LLM 을 부를 때 쓰는 최종값."""

    role: AgentRole
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str
    # 값마다 실제로 읽은 .env 키
    keys: dict[str, str] = field(default_factory=dict)

    def key(self, name: str) -> str:
        return self.keys.get(name, f"{_PREFIX[self.role]}_{name.upper()}")


class AgentSettings(AgentLLMSettings):
    """Agent가 쓰는 설정.

    app/core/agent_config.py의 AgentLLMSettings에 선언된 키 설정을
    역할별 LLMProfile로 바꾸는 규칙.
    빈 값 폴백, MODEL/BASE_URL 쌍 검증, 어느 키에서 읽었는지(keys).
    """

    # 빈 값은 "미설정"으로 보고 기본값을 사용
    @field_validator("MEMORY_REASONING_EFFORT", mode="before")
    @classmethod
    def _blank_to_default(cls, value: object) -> object:
        if not isinstance(value, str) or value.strip():
            return value
        return DEFAULT_REASONING_EFFORT

    # extra="ignore"로 설정(_ENV_FILE 에는 백엔드 키(APP_ENV 등)도 함께 들어 있음)
    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def profile(self, role: AgentRole = "supervisor") -> LLMProfile:
        """역할별 설정값을 반환한다.

        memory가 아닌 역할은 다음 기준으로 설정한다.
        - MODEL과 BASE_URL이 모두 있으면 해당 역할의 값을 사용한다.
        - 둘 다 없으면 MEMORY 설정을 사용한다.
        - 둘 중 하나만 있으면 AgentConfigError를 발생시킨다.
        - API_KEY가 없으면 MEMORY_API_KEY를 사용한다.
        - REASONING_EFFORT가 없으면 MEMORY_REASOING_EFFORT를 사용한다.
        - 공백만 있는 값은 설정되지 않은 것으로 처리한다.
        """

        if role not in _PREFIX:
            raise ValueError(f"알 수 없는 역할: {role!r}")
        if role == "memory":
            return self._memory_profile()

        prefix = _PREFIX[role]
        memory = self._memory_profile()
        model, base_url = self._own(f"{prefix}_MODEL"), self._own(f"{prefix}_BASE_URL")
        if bool(model) != bool(base_url):
            missing = f"{prefix}_BASE_URL" if model else f"{prefix}_MODEL"
            raise AgentConfigError(
                f"{prefix}_MODEL 과 {prefix}_BASE_URL은 함께 채운다. {missing}가 비어 있다. "
                "Memory 모델을 그대로 쓰려면 둘 다 비운다."
            )

        keys: dict[str, str] = {}
        if model:
            keys["model"], keys["base_url"] = f"{prefix}_MODEL", f"{prefix}_BASE_URL"
        else:
            model, base_url = memory.model, memory.base_url
            keys["model"], keys["base_url"] = "MEMORY_MODEL", "MEMORY_BASE_URL"

        api_key = self._own(f"{prefix}_API_KEY")
        keys["api_key"] = f"{prefix}_API_KEY" if api_key else "MEMORY_API_KEY"
        effort = self._own(f"{prefix}_REASONING_EFFORT")
        keys["reasoning_effort"] = (
            f"{prefix}_REASONING_EFFORT" if effort else "MEMORY_REASONING_EFFORT"
        )

        return LLMProfile(
            role=role,
            api_key=api_key or memory.api_key,
            base_url=base_url,
            model=model,
            reasoning_effort=effort or memory.reasoning_effort,
            keys=keys,
        )

    def _memory_profile(self) -> LLMProfile:
        return LLMProfile(
            role="memory",
            api_key=self._own("MEMORY_API_KEY"),
            base_url=self._own("MEMORY_BASE_URL"),
            model=self._own("MEMORY_MODEL"),
            reasoning_effort=self._own("MEMORY_REASONING_EFFORT"),
            keys={
                "api_key": "MEMORY_API_KEY",
                "base_url": "MEMORY_BASE_URL",
                "model": "MEMORY_MODEL",
                "reasoning_effort": "MEMORY_REASONING_EFFORT",
            },
        )

    def _own(self, key: str) -> str:
        """설정된 값만 돌려준다. 빈 값·None은 "미설정"이라 빈 문자열."""
        value = getattr(self, key)
        return str(value).strip() if value else ""


# 값이 없어도 import는 통과(실제 검증은 LLMClient 생성 시점에 진행)
# tool+schema 단위 테스트는 키 없이도 이 패키지를 import 해야함
@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
