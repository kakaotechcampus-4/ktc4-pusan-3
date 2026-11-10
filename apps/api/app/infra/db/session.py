from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 🚨 registry 는 import 자체가 목적이다 — 모든 도메인 모델을 메타데이터에 등록시킨다.
#    DB 를 쓰는 진입점은 앱이든 만료 정리 배치든 전부 이 모듈을 지나가므로, 여기 한 줄이면
#    어느 경로로 들어와도 메타데이터가 온전하다. main.py 에 두면 session.py 만 import
#    하는 배치에서 ForeignKey("child.id") 가 대상 테이블을 못 찾아 죽는다.
#    base.py 가 아니라 여기인 이유 — 모델이 base.Base 를 import 하므로 순환이 난다.
from app.infra.db import registry  # noqa: F401
from app.infra.db.url import build_url

# hide_parameters — DB 오류 글 끝의 [parameters: (...)] 를 숨긴다. INSERT 의 모든 값(부르는 이름 ·
# 원문)이 실리는 줄이다. 🚨 PostgreSQL 이 보낸 DETAIL 줄의 값은 못 숨긴다 — 그건 API 의
# DB 오류 처리기가 글을 아예 찍지 않는 것으로 막는다 (app/api/errors.py).
# pool_pre_ping — DB 가 재시작돼 끊긴 연결을 풀에서 꺼낼 때 한 번 확인하고 버린다. 없으면 DB 가
# 돌아온 뒤에도 첫 요청들이 죽은 연결을 받아 500 이 난다 (#671).
# server_settings — 쿼리 한 개 · 잠금 기다리기에 끝을 둔다 (ISSUE-F8-a). 없으면 API 핸들러 ·
# Curator · 배치가 멈춘 쿼리나 아이 잠금(pg_advisory_xact_lock)을 끝없이 기다린다.
# 넘기면 DBAPIError → 500. 값은 "멈춘 것만 끊는" 넉넉한 값이다 — 입력 run 은 러너 60초가 먼저
# 끊고, Curator 는 아이 잠금을 쥔 채 모델을 불러 남이 수십 초 기다릴 수 있어서(ISSUE-F8-b)
# 잠금 대기를 그보다 짧게 두면 정상 저장이 실패한다. 쿼리 사이의 모델 호출 시간은 세지 않는다.
# alembic 은 자기 엔진(alembic/env.py)이라 걸리지 않는다.
# ponytail: 한 값으로 API · 배치 · 적재 스크립트를 묶는다. 실측 뒤 API 만 조이려면 엔진을 나눈다
engine = create_async_engine(
    build_url("postgresql+asyncpg"),
    hide_parameters=True,
    pool_pre_ping=True,
    connect_args={"server_settings": {"statement_timeout": "120s", "lock_timeout": "60s"}},
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
