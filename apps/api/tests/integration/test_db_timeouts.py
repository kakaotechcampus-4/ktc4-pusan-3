"""DB 쿼리 · 잠금 대기에 끝이 있다 — 엔진의 server_settings (ISSUE-F8-a).

상한을 넘긴 쿼리가 DBAPIError 로 끝나는 것은 Postgres 의 동작이라 여기서 다시 보지 않는다.
우리 쪽 책임인 "엔진이 연결마다 값을 거는가" 만 본다.
"""

from sqlalchemy import text


async def test_엔진_연결에_시간_상한이_걸려_있다(session):
    assert await session.scalar(text("SHOW statement_timeout")) == "2min"
    assert await session.scalar(text("SHOW lock_timeout")) == "1min"
