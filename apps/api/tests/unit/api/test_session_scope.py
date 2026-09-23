"""SessionDep 는 엔드포인트 함수가 끝나면 닫힌다 — 응답을 다 보낼 때까지 기다리지 않는다.

FastAPI 기본값은 "응답을 다 보낸 뒤" 닫기라, SSE 처럼 오래 흐르는 응답이 인증 조회에 쓴 DB 연결을
스트림 내내 쥐고 있었다. 풀은 기본 5 + 넘침 10 이라 진행 화면 15개면 서버 전체가 멈춘다.
"""

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from app.api.deps.db import SessionDep
from app.infra.db.session import get_session


async def test_session_is_closed_before_the_stream_body():
    closed: list[bool] = []

    async def fake_session():
        try:
            yield object()
        finally:
            closed.append(True)

    app = FastAPI()
    app.dependency_overrides[get_session] = fake_session

    @app.get("/stream")
    async def stream(session: SessionDep) -> StreamingResponse:
        async def body():
            yield "closed" if closed else "open"

        return StreamingResponse(body())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stream")

    assert response.text == "closed"
