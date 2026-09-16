from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 🚨 registry 는 import 자체가 목적이다 — 모든 도메인 모델을 메타데이터에 등록시킨다.
#    DB 를 쓰는 진입점은 앱이든 만료 정리 배치든 전부 이 모듈을 지나가므로, 여기 한 줄이면
#    어느 경로로 들어와도 메타데이터가 온전하다. main.py 에 두면 session.py 만 import
#    하는 배치에서 ForeignKey("child.id") 가 대상 테이블을 못 찾아 죽는다.
#    base.py 가 아니라 여기인 이유 — 모델이 base.Base 를 import 하므로 순환이 난다.
from app.infra.db import registry  # noqa: F401
from app.infra.db.url import build_url

engine = create_async_engine(build_url("postgresql+asyncpg"))

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
