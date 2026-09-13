"""테스트 공통 픽스처.

픽스처가 셋이다.
    client     — DB 를 쓰지 않는 테스트용. 서버도 DB 도 띄우지 않고 돈다
    session    — 트랜잭션 하나를 열고 테스트가 끝나면 롤백한다 (DB 필요)
    db_client  — client + session. A-01~A-20 처럼 DB 를 보는 테스트가 쓴다
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.db.session import engine, get_session
from app.main import app


@pytest.fixture
async def client():
    """ASGI 앱을 직접 호출하는 클라이언트. 별도 서버 포트를 열지 않는다.

    DB 커넥션은 첫 쿼리에서 열리므로(lazy), DB 를 보지 않는 엔드포인트 테스트는
    Postgres 없이도 통과한다. DB 를 보는 테스트는 아래 db_client 를 쓸 것.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def session():
    """테스트마다 트랜잭션 하나. 끝나면 롤백해 다음 테스트에 흔적을 남기지 않는다.

    세션을 이미 시작된 커넥션 트랜잭션에 묶으므로 핸들러가 commit() 해도
    바깥 트랜잭션은 남고,
    마지막 rollback() 이 테스트 중 변경을 전부 되돌린다.
    """
    async with engine.connect() as conn:
        tx = await conn.begin()
        factory = async_sessionmaker(bind=conn, expire_on_commit=False)
        async with factory() as s:
            yield s
        await tx.rollback()


@pytest.fixture
async def db_client(session: AsyncSession):
    """DB 를 보는 엔드포인트 테스트용.

    🚨 dependency_overrides 의 "키" 는 함수 객체 자체다. 라우터가
       Depends(get_session) 으로 적어둔 그 함수를 위 session 픽스처로 갈아 끼운다.
       이 줄이 없으면 요청 핸들러가 커넥션을 따로 열어서 롤백이 안 먹고,
       A-01("parent 미생성")같은 검증이 다음 테스트로 흘러넘친다.
    """
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
