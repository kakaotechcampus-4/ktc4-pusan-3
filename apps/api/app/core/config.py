from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    APP_ENV: str = "local"
    APP_NAME: str = "ktc4-pusan-3-api"

    # 급식표 OCR — Elice MLAPI (OpenAI 호환 게이트웨이). 비어 있으면 OCR 기능만 비활성.
    MLAPI_BASE_URL: str | None = None
    MLAPI_API_KEY: str | None = None
    MEAL_OCR_MODEL: str = "gemini-3.1-pro-preview"

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8"}


settings = Settings()
