"""LLM 을 부르는 역할의 설정 묶음.

역할 하나가 4개의 키를 갖는다.
    <역할>_API_KEY · <역할>_BASE_URL · <역할>_MODEL · <역할>_REASONING_EFFORT

역할이 늘면 AGENT_ROLES에 이름만 더하고,
그 목록을 core Settings와 AgentSettings가 함께 상속한다.
"""

from typing import Literal, get_args

from pydantic import ValidationInfo, create_model, field_validator
from pydantic_settings import BaseSettings

# LLM을 부르는 역할
# 🚨 역할을 늘릴 때 고치는 곳은 이 한 줄이다 (activity · growth · health).
#    env 키 · core Settings · AgentSettings · _PREFIX 가 전부 여기서 파생된다.
AgentRole = Literal["memory", "supervisor", "food"]

# env 키 접두사. AgentRole 에서 만든다 — 두 곳에 적으면 어긋난다
AGENT_ROLES: tuple[str, ...] = tuple(role.upper() for role in get_args(AgentRole))

# 역할 하나가 갖는 항목
LLM_FIELDS: tuple[str, ...] = ("API_KEY", "BASE_URL", "MODEL", "REASONING_EFFORT")

# 역할과 무관한 호출 튜닝값. 비우면 LLMClient의 기본값 사용
_SHARED: dict[str, object] = {
    "LLM_TIMEOUT_S": 60.0,  # 한 번의 chat 호출 상한 (초)
    "LLM_MAX_RETRIES": 2,  # SDK 내부 재시도 (429, 5xx 대상)
}


def role_keys(role: str) -> tuple[str, ...]:
    """역할 하나가 쓰는 키 이름 넷. MEMORY -> MEMORY_API_KEY … MEMORY_REASONING_EFFORT"""
    return tuple(f"{role}_{name}" for name in LLM_FIELDS)


def agent_llm_keys() -> tuple[str, ...]:
    """모든 역할의 키 이름."""
    return tuple(key for role in AGENT_ROLES for key in role_keys(role))


def declared_keys() -> tuple[str, ...]:
    """이 모듈이 선언하는 키 전체. .env.example 과 대조할 때 쓴다."""
    return agent_llm_keys() + tuple(_SHARED)


def _blank_is_default(value: object, info: ValidationInfo) -> object:
    """빈 칸은 "안 채운 것"으로 보고 기본값을 쓴다.

    .env.example 을 그대로 복사하면 숫자 항목이 빈 문자열로 들어온다. 그대로 두면
    float_parsing 으로 부팅이 멈추는데, 이건 값이 틀린 게 아니라 안 채운 것이다.
    """
    if isinstance(value, str) and not value.strip():
        return _SHARED[str(info.field_name)]
    return value


AgentLLMSettings = create_model(
    "AgentLLMSettings",
    __base__=BaseSettings,
    __doc__=(
        "역할별 LLM 키 선언. AGENT_ROLES × LLM_FIELDS 로 만들어진다.\n"
        "core Settings 와 AgentSettings 가 함께 상속해서 목록이 갈라지지 않게 한다."
    ),
    **{key: (str, "") for key in agent_llm_keys()},
    **{key: (type(default), default) for key, default in _SHARED.items()},
    __validators__={
        "_shared_blank_is_default": field_validator(*_SHARED, mode="before")(_blank_is_default),
    },
)
