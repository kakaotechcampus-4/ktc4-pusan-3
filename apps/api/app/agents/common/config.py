# Agent 레이어 전용 설정
# 같은 `apps/api/.env` 를 읽되 Agent 가 쓰는 키만 여기서 다룬다

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

DEFAULT_REASONING_EFFORT = "none"


class AgentSettings(BaseSettings):
    MEMORY_API_KEY: str = ""                 # Elice Serverless API Key
    MEMORY_BASE_URL: str = ""                # ML API endpoint(/v1 까지 포함)
    MEMORY_MODEL: str = ""                   # 모델 ID
    MEMORY_REASONING_EFFORT: str = DEFAULT_REASONING_EFFORT

    LLM_TIMEOUT_S: float = 60.0      # 한 번의 chat 호출 상한
    LLM_MAX_RETRIES: int = 2         # SDK 내부 재시도 (429, 5xx 대상)

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


# 값이 없어도 import는 통과(실제 검증은 LLMClient 생성 시점에 진행)
# tool+schema 단위 테스트는 키 없이도 이 패키지를 import 해야함
@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
