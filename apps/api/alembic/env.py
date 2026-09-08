from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.infra.db.base import Base
from app.infra.db.url import build_url

# 모델 모듈 import — autogenerate 인식을 위해 여기에 추가
# from app.domains.child import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

ALEMBIC_URL = build_url("postgresql+psycopg")

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
