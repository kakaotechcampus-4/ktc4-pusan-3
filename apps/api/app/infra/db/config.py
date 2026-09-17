import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationInfo, field_validator

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# .env 파일 로드 (존재하면)
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)


class AlembicSettings(BaseModel):
    """Alembic(마이그레이션)용 최소 설정. DB만 검증한다."""

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str  # 필수
    DB_PASSWORD: str  # 필수
    DB_NAME: str  # 필수

    @field_validator("DB_USER", "DB_PASSWORD", "DB_NAME")
    @classmethod
    def _require_db_values(cls, value: str, info: ValidationInfo) -> str:
        """필수 DB 값 검증. 빈 문자열도 거부한다."""
        if not value or not value.strip():
            raise ValueError(
                f"{info.field_name} 이(가) 비어 있습니다. "
                "예: DB_USER=dev DB_PASSWORD=dev DB_NAME=dailyagent"
            )
        return value.strip()


def get_alembic_settings() -> AlembicSettings:
    """환경변수에서 DB 설정을 읽는다 (.env 파일 로드 후)."""
    try:
        return AlembicSettings(
            DB_HOST=os.getenv("DB_HOST", "localhost"),
            DB_PORT=int(os.getenv("DB_PORT", "5432")),
            DB_USER=os.getenv("DB_USER", ""),
            DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
            DB_NAME=os.getenv("DB_NAME", ""),
        )
    except Exception as error:
        raise RuntimeError(f"Alembic 설정 오류: {error}") from None
