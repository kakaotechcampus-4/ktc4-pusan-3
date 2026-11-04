"""운영용 헬스체크 — /health 는 프로세스만, /health/ready 는 DB · Redis 까지 (#671).

/health/ready 의 DB 확인은 실제 개발 DB 에 SELECT 1 을 보낸다. Redis 는 fakeredis(정상)와
아무것도 듣지 않는 포트(장애)로 바꿔 끼운다.
"""

import asyncio

import fakeredis
import pytest
import redis

from app.api import health

_down = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
"""아무것도 듣지 않는 포트 — PING 하면 진짜 ConnectionError 가 난다."""


@pytest.fixture
def redis_ok(monkeypatch):
    monkeypatch.setattr(health, "get_redis", lambda: fakeredis.FakeRedis())


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "env" in body


async def test_ready_는_DB_와_Redis_가_되면_200(client, redis_ok):
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}


async def test_ready_는_Redis_가_죽으면_503_이고_health_는_200(client, monkeypatch):
    monkeypatch.setattr(health, "get_redis", lambda: _down)

    ready = await client.get("/health/ready")
    alive = await client.get("/health")

    assert ready.status_code == 503
    assert ready.json() == {"status": "fail", "checks": {"db": "ok", "redis": "fail"}}
    assert alive.status_code == 200  # docker healthcheck 가 api 를 재시작하지 않는다
    assert "127.0.0.1" not in ready.text  # 접속 주소 · 오류 글은 내지 않는다


async def test_ready_는_DB_가_죽으면_503(client, monkeypatch, redis_ok):
    async def db_down():
        raise OSError("connection refused to db:5432")

    monkeypatch.setattr(health, "_db", db_down)

    ready = await client.get("/health/ready")

    assert ready.status_code == 503
    assert ready.json()["checks"] == {"db": "fail", "redis": "ok"}
    assert "5432" not in ready.text


async def test_ready_는_매달린_의존성을_짧게_끊는다(client, monkeypatch, redis_ok):
    async def hang():
        await asyncio.sleep(60)

    monkeypatch.setattr(health, "_db", hang)
    monkeypatch.setattr(health, "READY_TIMEOUT_S", 0.1)

    ready = await asyncio.wait_for(client.get("/health/ready"), timeout=5)

    assert ready.status_code == 503


async def test_REDIS_URL_이_없으면_Redis_는_검사하지_않는다(client, monkeypatch):
    monkeypatch.setattr(health, "get_redis", lambda: None)  # 메모리 폴백이 정상 구성이다

    ready = await client.get("/health/ready")

    assert ready.status_code == 200
