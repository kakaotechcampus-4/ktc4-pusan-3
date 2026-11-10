"""DB 쿼리 · 잠금 대기에 끝이 있다 — 엔진의 server_settings (ISSUE-F8-a).

상한을 넘긴 쿼리가 끊기는 것은 Postgres 의 동작이라 다시 보지 않는다. 우리 쪽 책임인
"엔진이 연결마다 값을 거는가" 와 "걸렸을 때 로그에 남는가" 만 본다.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infra.db.session import engine


async def test_엔진_연결에_시간_상한이_걸려_있다(session):
    assert await session.scalar(text("SHOW statement_timeout")) == "2min"
    assert await session.scalar(text("SHOW lock_timeout")) == "1min"


async def test_상한에_걸리면_어느_상한인지_로그에_남는다(caplog):
    async with engine.connect() as conn, conn.begin():
        await conn.execute(text("SET LOCAL statement_timeout = '50ms'"))
        with pytest.raises(DBAPIError):
            await conn.execute(text("SELECT pg_sleep(1)"))
    assert "쿼리 2분 상한(statement_timeout)" in caplog.text
