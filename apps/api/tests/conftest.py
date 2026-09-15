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
        factory = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            # 🚨 이 인자가 없으면 핸들러의 rollback 이 바깥 트랜잭션까지 되감는다.
            #    A-13(signup 실패 → parent 미생성)처럼 롤백을 확인하는 테스트가
            #    그 뒤 단언에서 죽은 커넥션을 본다. savepoint 로 받아 격리한다.
            join_transaction_mode="create_savepoint",
        )
        async with factory() as s:
            yield s
        await tx.rollback()

    # 🚨 커넥션 풀을 비운다. pytest-asyncio 는 테스트마다 새 이벤트 루프를 열고, asyncpg
    #    커넥션은 자기를 만든 루프에 묶여 있다. 풀에 남겨 두면 다음 테스트가 다른 루프에서
    #    같은 커넥션을 꺼내 "another operation is in progress" 로 죽는다.
    #    단독 실행은 통과하고 모아서 돌리면 두 번째부터 깨지는 증상이 이것이다.
    #
    # TODO: DB 테스트가 50개쯤으로 늘면 asyncio_default_fixture_loop_scope=session 으로
    #   옮기고 이 줄을 뺀다. dispose 는 매번 커넥션 풀을 버리는 비용이 있다. 지금은 18개라
    #   그 비용보다, pytest-asyncio 설정을 바꿔 저장소의 모든 비동기 테스트 실행 방식을
    #   건드리는 쪽이 크다.
    await engine.dispose()


@pytest.fixture
async def db_client(client: AsyncClient, session: AsyncSession):
    """DB 를 보는 엔드포인트 테스트용. 위 client 에 세션 오버라이드만 얹는다.

    🚨 dependency_overrides 의 "키" 는 함수 객체 자체다. 라우터가
       Depends(get_session) 으로 적어둔 그 함수를 위 session 픽스처로 갈아 끼운다.
       이 줄이 없으면 요청 핸들러가 커넥션을 따로 열어서 롤백이 안 먹고,
       A-01("parent 미생성")같은 검증이 다음 테스트로 흘러넘친다.

    🚨 정리는 pop 으로 이 키만 지운다. clear() 로 비우면 다른 픽스처가 끼워 둔
       오버라이드(A-12·A-16 에서 쓸 카카오 integration 스텁 등)까지 함께 사라진다.
    """
    app.dependency_overrides[get_session] = lambda: session
    yield client
    app.dependency_overrides.pop(get_session, None)
