from pathlib import Path

from pydantic import ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class AlembicSettings(BaseSettings):
    """Alembic(마이그레이션)용 최소 설정. DB 값만 읽고 검증한다.

    apps/api/.env 는 앱 전체가 같이 쓰는 파일이라 DB 외의 키가 섞여 있다 — extra="ignore".
    오타 검사는 core Settings(extra="forbid")가 맡는다 (#59).

    🚨 load_dotenv 를 쓰지 않는다. .env 전체가 os.environ 에 올라가면
       Settings(_env_file=None) 으로 격리한 테스트에 값이 샌다.
    """

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    @field_validator("DB_USER", "DB_PASSWORD", "DB_NAME")
    @classmethod
    def _require_db_values(cls, value: str, info: ValidationInfo) -> str:
        """필수 DB 값 검증. 빈 문자열도 거부한다."""
        if not value.strip():
            raise ValueError(
                f"{info.field_name} 이(가) 비어 있습니다. "
                "예: DB_USER=dev DB_PASSWORD=dev DB_NAME=dailyagent"
            )
        return value.strip()


def get_alembic_settings() -> AlembicSettings:
    """환경변수와 .env 에서 DB 설정만 읽는다. os.environ 은 건드리지 않는다."""
    try:
        return AlembicSettings()
    except ValidationError as error:
        raise RuntimeError(f"Alembic 설정 오류: {error}") from None
