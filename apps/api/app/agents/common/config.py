# Agent 레이어 전용 설정
# 같은 `apps/api/.env` 를 읽되 Agent 가 쓰는 키만 여기서 다룬다

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

DEFAULT_REASONING_EFFORT = "none"

# LLM을 부르는 역할. 역할의 키가 비어 있으면 MEMORY_*를 사용
AgentRole = Literal["memory", "supervisor", "food"]
_PREFIX: dict[str, str] = {"memory": "MEMORY", "supervisor": "SUPERVISOR", "food": "FOOD"}


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


class AgentSettings(BaseSettings):
    # 기본 설정. 다른 역할의 키가 비어 있으면 이 값을 쓴다
    MEMORY_API_KEY: str = ""  # Elice Serverless API Key
    MEMORY_BASE_URL: str = ""  # ML API endpoint(/v1 까지 포함)
    MEMORY_MODEL: str = ""  # 모델 ID
    MEMORY_REASONING_EFFORT: str = DEFAULT_REASONING_EFFORT

    # Supervisor/Food — 전부 선택. MODEL과 BASE_URL은 한 쌍으로 채운다
    # (BASE_URL 이 모델마다 다르다. 한쪽만 채우면 LLMClient 가 거절한다)
    SUPERVISOR_API_KEY: str = ""
    SUPERVISOR_BASE_URL: str = ""
    SUPERVISOR_MODEL: str = ""
    SUPERVISOR_REASONING_EFFORT: str = ""

    FOOD_API_KEY: str = ""
    FOOD_BASE_URL: str = ""
    FOOD_MODEL: str = ""
    FOOD_REASONING_EFFORT: str = ""

    LLM_TIMEOUT_S: float = 60.0  # 한 번의 chat 호출 상한
    LLM_MAX_RETRIES: int = 2  # SDK 내부 재시도 (429, 5xx 대상)

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
        return str(getattr(self, key)).strip()


# 값이 없어도 import는 통과(실제 검증은 LLMClient 생성 시점에 진행)
# tool+schema 단위 테스트는 키 없이도 이 패키지를 import 해야함
@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
