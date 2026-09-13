from sqlalchemy.engine import URL

from app.core.config import settings


def build_url(driver: str) -> URL:
    return URL.create(
        driver,
        username=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
    )
