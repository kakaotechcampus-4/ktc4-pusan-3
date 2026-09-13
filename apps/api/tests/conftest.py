"""테스트 공통 픽스처.

Spring 대응: @SpringBootTest + @Transactional(테스트 후 롤백) + @MockBean.

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
    """ASGI 직결 클라이언트 — Spring 의 MockMvc 에 가깝다. 포트를 열지 않는다.

    DB 커넥션은 첫 쿼리에서 열리므로(lazy), DB 를 보지 않는 엔드포인트 테스트는
    Postgres 없이도 통과한다. DB 를 보는 테스트는 아래 db_client 를 쓸 것.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# REVIEW: 이 픽스처는 DB 통합 테스트마다 실제 PostgreSQL 연결과 트랜잭션을 만든다.
# 현재 CI에는 PostgreSQL 실행 설정이 없어 db_client를 쓰는 테스트가 추가되면 실패할 수 있다.
# 확인할 내용: CI에 PostgreSQL을 실행할지, DB 통합 테스트를 기본 테스트에서 분리할지 결정한다.
# 검증 테스트 없음: 현재 session 또는 db_client 픽스처를 사용하는 테스트가 없다.
@pytest.fixture
async def session():
    """테스트마다 트랜잭션 하나. 끝나면 롤백해 다음 테스트에 흔적을 남기지 않는다.

    Spring 의 @Transactional 테스트와 같은 효과를 손으로 만든 것이다. 커넥션에 묶은
    세션이라 핸들러 안의 commit() 은 SAVEPOINT 로 처리되고(SQLAlchemy 기본
    join_transaction_mode="conditional_savepoint"), 바깥 rollback() 이 전부 되돌린다.
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
