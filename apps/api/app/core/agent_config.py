"""LLM 을 부르는 역할의 설정 묶음.

역할 하나가 4개의 키를 갖는다.
    <역할>_API_KEY · <역할>_BASE_URL · <역할>_MODEL · <역할>_REASONING_EFFORT

이 목록을 core Settings와 AgentSettings가 함께 상속한다.
값을 읽어 쓰는 곳과 역할별 폴백 규칙(빈 값이면 MEMORY_* 사용 등)은
app/agents/common/config.py의 AgentSettings.
"""

from typing import Literal, get_args

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings

# LLM을 부르는 역할
# 역할 확장 -> 이 줄과 아래 AgentLLMSettings의 키 4줄을 함께 더함
AgentRole = Literal["memory", "supervisor", "food"]

# env 키 접두사. AgentRole 에서 만든다 — 두 곳에 적으면 어긋난다
AGENT_ROLES: tuple[str, ...] = tuple(role.upper() for role in get_args(AgentRole))

# 역할 하나가 갖는 항목
LLM_FIELDS: tuple[str, ...] = ("API_KEY", "BASE_URL", "MODEL", "REASONING_EFFORT")


class AgentLLMSettings(BaseSettings):
    """역할별 LLM 키 선언. core Settings 와 AgentSettings 가 함께 상속한다."""

    MEMORY_API_KEY: str = ""
    MEMORY_BASE_URL: str = ""
    MEMORY_MODEL: str = ""
    MEMORY_REASONING_EFFORT: str = ""

    SUPERVISOR_API_KEY: str = ""
    SUPERVISOR_BASE_URL: str = ""
    SUPERVISOR_MODEL: str = ""
    SUPERVISOR_REASONING_EFFORT: str = ""

    FOOD_API_KEY: str = ""
    FOOD_BASE_URL: str = ""
    FOOD_MODEL: str = ""
    FOOD_REASONING_EFFORT: str = ""

    # 역할과 무관한 호출 튜닝값
    LLM_TIMEOUT_S: float = 60.0  # 한 번의 chat 호출 상한 (초)
    LLM_MAX_RETRIES: int = 2  # SDK 내부 재시도 (429, 5xx 대상)

    @field_validator("LLM_TIMEOUT_S", "LLM_MAX_RETRIES", mode="before")
    @classmethod
    def _blank_is_default(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[str(info.field_name)].default
        return value
