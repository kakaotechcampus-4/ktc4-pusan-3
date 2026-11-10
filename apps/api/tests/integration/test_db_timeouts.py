"""DB 쿼리 · 잠금 대기에 끝이 있다 — 엔진의 server_settings (ISSUE-F8-a).

상한을 넘긴 쿼리가 끊기는 것은 Postgres 의 동작이라 다시 보지 않는다. 우리 쪽 책임인
"엔진이 연결마다 값을 거는가" 와 "걸렸을 때 로그에 남는가" 만 본다.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infra.db.session import engine


async def test_엔진_연결에_시간_상한이_걸려_있다(session):
    assert await session.scalar(text("SHOW statement_timeout")) == "2min"
    # 잠금 대기는 쿼리 상한 안에서 같이 끊긴다 — 따로 짧게 걸면 Curator 를 기다리는 정상 저장이
    # 실패한다 (session.py)
    assert await session.scalar(text("SHOW lock_timeout")) == "0"


async def test_상한에_걸리면_어느_상한인지_로그에_남는다(caplog):
    async with engine.connect() as conn, conn.begin():
        await conn.execute(text("SET LOCAL statement_timeout = '50ms'"))
        with pytest.raises(DBAPIError):
            await conn.execute(text("SELECT pg_sleep(1)"))
    assert "쿼리 2분 상한(statement_timeout)" in caplog.text


async def test_취소된_쿼리는_시간_상한으로_적지_않는다(caplog):
    async with engine.connect() as conn:
        pid = await conn.scalar(text("SELECT pg_backend_pid()"))

        async def cancel() -> None:
            await asyncio.sleep(0.3)
            async with engine.connect() as other:
                await other.execute(text("SELECT pg_cancel_backend(:pid)"), {"pid": pid})

        task = asyncio.create_task(cancel())
        with pytest.raises(DBAPIError):
            await conn.execute(text("SELECT pg_sleep(3)"))
        await task
    assert "시간 상한이 아니다(57014)" in caplog.text
    assert "쿼리 2분 상한" not in caplog.text
