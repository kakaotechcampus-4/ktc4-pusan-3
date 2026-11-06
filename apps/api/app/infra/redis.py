"""Redis 연결 — REDIS_URL 이 있을 때만 연결한다.

REDIS_URL 이 비어 있으면 None 을 돌려준다. 호출부(idempotency 등)는 None 이면
프로세스 메모리 폴백을 쓴다 — 단일 워커 전제 (#134).
"""

import logging
import time
from urllib.parse import urlparse

import redis

from app.core.config import settings

log = logging.getLogger(__name__)

_client: redis.Redis | None = None

TIMEOUT_S = 1.0
"""연결 · 응답 한 번의 상한(초). 같은 호스트라 평소엔 1ms 안쪽이다 (#671).
새 연결도 왕복이 늘지 않게 HELLO · CLIENT SETINFO 를 보내지 않는다 — 보내면 느린 Redis 에서
왕복마다 상한을 기다려 몇 배가 된다 (#729 리뷰). 그래서 한 호출은 연결 + 명령, 최대 2배다."""

OPEN_S = 10.0
"""차단기. 연결 · 시간 초과 오류가 한 번 나면 이 시간 동안 Redis 를 부르지 않고 바로 ConnectionError
— 장애 중에 호출마다 상한을 기다리지 않는다. idempotency 는 503, 도서 검색어 캐시는 건너뛴다.
동기 클라이언트라 루프에서 부르는 곳(러너의 forget_run · 도서 캐시)은 기다리는 동안 루프가 멈춘다."""


class _Breaker(redis.Redis):
    # ponytail: 파이프라인은 거치지 않는다 — 지금 쓰는 곳(idempotency.remember)은 앞의 recall 이 막는다
    _open_until = 0.0

    def execute_command(self, *args, **options):
        if time.monotonic() < self._open_until:
            raise redis.ConnectionError("Redis 차단기 열림")
        try:
            return super().execute_command(*args, **options)
        except (redis.ConnectionError, redis.TimeoutError):
            self._open_until = time.monotonic() + OPEN_S
            raise


def get_redis() -> redis.Redis | None:
    """Redis 클라이언트를 돌려준다. REDIS_URL 이 없으면 None."""
    global _client
    if _client is not None:
        return _client
    if not settings.REDIS_URL:
        return None
    _client = _Breaker.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=TIMEOUT_S,
        socket_timeout=TIMEOUT_S,
        protocol=2,
        driver_info=None,
    )
    parsed = urlparse(settings.REDIS_URL)
    log.info("Redis 연결: %s:%s/%s", parsed.hostname, parsed.port, parsed.path.lstrip("/"))
    return _client
