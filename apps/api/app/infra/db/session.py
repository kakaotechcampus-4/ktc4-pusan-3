import logging
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.engine import ExceptionContext
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
# server_settings — 쿼리 한 개에 2분 끝을 둔다 (#936). 없으면 API 핸들러 · Curator · 배치가 멈춘
# 쿼리나 아이 잠금(pg_advisory_xact_lock)을 끝없이 기다린다. 넘기면 DBAPIError → 500.
# 잠금 대기도 쿼리 시간에 들어가서 이 값 하나로 같이 끊긴다.
# 🚨 lock_timeout 을 따로 짧게 걸지 않는다 — Curator 가 아이 잠금을 쥔 채 모델을 부르고(#937)
#    모델 한 번이 60초 · 재시도 2번이라, 1분 상한이면 같은 아이의 정상 저장이 기다리다 실패했다
#    (65초 쥔 잠금 → 60초에 55P03 재현, #941 리뷰). 2분도 Curator 가 그보다 오래 쥐면 끊긴다 —
#    #937 이 잠금 밖으로 모델 호출을 빼면 이 값을 조인다.
# 입력 run 은 러너 60초가 먼저 끊는다. 쿼리 사이의 모델 호출 시간은 세지 않는다.
# alembic 은 자기 엔진(alembic/env.py)이라 걸리지 않는다.
# ponytail: 한 값으로 API · 배치 · 적재 스크립트를 묶는다. 실측 뒤 API 만 조이려면 엔진을 나눈다
engine = create_async_engine(
    build_url("postgresql+asyncpg"),
    hide_parameters=True,
    pool_pre_ping=True,
    connect_args={"server_settings": {"statement_timeout": "120s"}},
)

log = logging.getLogger(__name__)


@event.listens_for(engine.sync_engine, "handle_error")
def _log_timeout(ctx: ExceptionContext) -> None:
    """상한에 걸린 쿼리를 경로와 상관없이 한 줄로 남긴다 — API · Curator · 배치가 다 이 엔진을
    지난다. Curator 는 오류 종류만, API 처리기는 sqlstate 만 찍어서 상한에 걸린 건지 따로 안 보였다.

    57014 는 시간 상한과 취소(pg_cancel_backend · 드라이버 취소)가 같은 코드라, Postgres 오류
    문구("statement timeout")로 가른다. 이 문구는 DB 의 lc_messages 를 따라 번역된다 — 우리 DB 는
    기본값(en_US.utf8)이라 영어다. 바꾸면 시간 상한도 "취소됐다" 로 찍힌다.
    🚨 그 글도 쿼리 · 값도 싣지 않는다 (hide_parameters 와 같은 이유).
    """
    if getattr(ctx.original_exception, "sqlstate", None) != "57014":
        return
    if "statement timeout" in str(ctx.original_exception):
        log.warning("DB 시간 상한에 걸려 끊었다 — 쿼리 2분 상한(statement_timeout)")
    else:
        log.warning("DB 쿼리가 취소됐다 — 시간 상한이 아니다(57014)")


async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
