"""Redis 연결 — REDIS_URL 이 있을 때만 연결한다.

REDIS_URL 이 비어 있으면 None 을 돌려준다. 호출부(idempotency 등)는 None 이면
프로세스 메모리 폴백을 쓴다 — 단일 워커 전제 (#134).
"""

import logging
from urllib.parse import urlparse

import redis

from app.core.config import settings

log = logging.getLogger(__name__)

_client: redis.Redis | None = None

TIMEOUT_S = 1.0
"""연결 · 응답 상한(초). 동기 클라이언트라 기다리는 동안 이벤트 루프가 멈춘다 — Redis 가 응답 없이
매달리면 요청 전체가 같이 멈추므로 짧게 끊고 호출부가 폴백한다 (#671).
같은 호스트라 평소엔 1ms 안쪽이다."""


def get_redis() -> redis.Redis | None:
    """Redis 클라이언트를 돌려준다. REDIS_URL 이 없으면 None."""
    global _client
    if _client is not None:
        return _client
    if not settings.REDIS_URL:
        return None
    _client = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=TIMEOUT_S,
        socket_timeout=TIMEOUT_S,
    )
    parsed = urlparse(settings.REDIS_URL)
    log.info("Redis 연결: %s:%s/%s", parsed.hostname, parsed.port, parsed.path.lstrip("/"))
    return _client
