from sqlalchemy.engine import URL

from app.core.config import settings
from app.infra.db.config import get_alembic_settings


def build_url(driver: str) -> URL:
    """앱 실행 시: 전체 검증된 settings 사용"""
    return URL.create(
        driver,
        username=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
    )


def build_url_from_env(driver: str) -> URL:
    """Alembic 마이그레이션: DB만 검증"""
    alembic_config = get_alembic_settings()

    return URL.create(
        driver,
        username=alembic_config.DB_USER,
        password=alembic_config.DB_PASSWORD,
        host=alembic_config.DB_HOST,
        port=alembic_config.DB_PORT,
        database=alembic_config.DB_NAME,
    )
