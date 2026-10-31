from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# 모델 등록 — autogenerate 가 전체 스키마를 보려면 모든 모델이 import 돼 있어야 한다.
# 목록은 app/infra/db/registry.py 한 곳에만 둔다. 운영 앱도 같은 모듈을 쓴다.
from app.infra.db import registry  # noqa: F401
from app.infra.db.base import Base
from app.infra.db.url import build_url_from_env

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

ALEMBIC_URL = build_url_from_env("postgresql+psycopg")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=ALEMBIC_URL.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(ALEMBIC_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
