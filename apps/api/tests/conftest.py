"""테스트 공통 픽스처.

픽스처가 셋이다.
    client     — DB 를 쓰지 않는 테스트용. 서버도 DB 도 띄우지 않고 돈다
    session    — 트랜잭션 하나를 열고 테스트가 끝나면 롤백한다 (DB 필요)
    db_client  — client + session. A-01~A-20 처럼 DB 를 보는 테스트가 쓴다

Discord 알림은 tests/__init__.py 가 끈다 — 이 파일이 app.main 을 불러오기 전에 돌아야 해서
거기 있다.
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

    # 커넥션 풀은 비우지 않는다. asyncpg 커넥션은 자기를 만든 이벤트 루프에 묶여 있는데,
    # 테스트 전체가 루프 하나를 같이 쓰므로(pyproject asyncio_default_*_loop_scope = session)
    # 다음 테스트가 같은 커넥션을 꺼내도 된다. 예전에는 테스트마다 루프가 새로 열려서
    # 매번 engine.dispose() 로 풀을 버렸다 (#257).


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


@pytest.fixture
def hung_redis_url():
    """연결은 받고 아무 말도 안 하는 서버의 주소 — Redis 가 응답 없이 멈춘 것처럼 (#671).

    연결 거부(즉시 실패)와 달리 응답 상한이 있어야 끝난다. 받은 연결은 닫지 않고 쥐고 있는다.
    """
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"redis://127.0.0.1:{server.getsockname()[1]}/0"
    server.close()
    for conn in held:
        conn.close()


@pytest.fixture(autouse=True)
def _no_run_channel_mirror():
    """run 채널의 표 미러(#877 4단계)를 끈다 — 메모리 채널만으로 돈다.

    미러는 자기 연결로 백그라운드에서 쓰므로 테스트 트랜잭션 밖이다. 켜 두면 테스트가 만든
    (커밋 안 된) 보호자를 못 봐 FK 오류가 나고, 끝나도 행이 남는다. 미러 자체는
    tests/integration/api/test_run_channel_mirror.py 가 실제로 커밋하는 연결로 켜서 본다.

    🚨 monkeypatch 를 받지 않는다. 모든 테스트가 받는 픽스처가 monkeypatch 를 먼저 열면, 그 테스트의
       다른 픽스처보다 monkeypatch 되돌리기가 늦어져 정리 순서가 바뀐다(test_llm_usage 가 깨졌다).
    """
    from app.api.runs import registry

    saved = registry._session_factory
    registry._session_factory = None
    yield
    registry._session_factory = saved


@pytest.fixture(autouse=True)
def _medication_on():
    """스토어 정책으로 꺼 둔 복약(#923)을 테스트에서는 켠다 — 기존 테스트는 켜진 동작을 건다.

    꺼 둔 것은 다시 켤 코드라서 건너뛰지 않고 지킨다. 꺼진 동작은
    tests/unit/agents/health/test_health_medication_off.py 가 끄고 본다.
    🚨 monkeypatch 를 받지 않는다 — 바로 위 픽스처와 같은 이유다.
    """
    from app.agents.health import registry

    saved = registry.MEDICATION_ENABLED
    registry.MEDICATION_ENABLED = True
    yield
    registry.MEDICATION_ENABLED = saved
