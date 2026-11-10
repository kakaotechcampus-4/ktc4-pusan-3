"""Idempotency-Key 기억 — Redis 또는 프로세스 메모리 폴백.

계약서 §01 은 "되돌릴 수 없는 POST 는 헤더가 없으면 400" 까지만 정한다.
docs/api/idempotency-v1.md 가 그 위에 제안한 동작 중 **"같은 키 · 같은 보호자 → 처음 응답 재생"**
까지만 여기서 한다.
프론트 mutation 은 retry: false 라 중복은 보호자가 버튼을 다시 누를 때만 생기고, 그때 재생이
없으면 같은 한 줄이 관찰 N건씩 두 번 저장된다.

- 스코프는 (parent_id, method, path, key) — 보호자 사이에 키가 섞이지 않는다 (§3-5)
- 422(같은 키 · 다른 본문) · 409(처리 중) · 24시간 보관은 다음 이슈. 409 는 접수가 즉시 끝나는
  구조라 창이 몇 ms 뿐이고, 키를 **러너를 띄우기 전에** 적어 두면 그 창도 재생으로 흡수된다

REDIS_URL 이 설정되면 Redis 를 쓰고, 없으면 프로세스 메모리 dict 로 폴백한다(로컬 · 테스트).
폴백은 단일 워커 전제다 (#134).

🚨 Redis 가 **죽으면** 메모리로 넘어가지 않고 503 `temporarily_unavailable` 로 막는다 (#671).
   장애 직전에 Redis 에 적힌 키는 메모리에 없어서, 메모리로 받으면 같은 키 재시도가 새 요청이 돼
   두 번 저장된다. 서버를 여러 대로 늘리면 메모리는 다른 서버로 간 재시도도 못 막는다 — 503 은
   서버 대수와 상관없이 같다. 다시 시도하면 되는 오류라 같은 키로 다시 보내면 된다.
   로그에는 오류 종류만 남긴다 (키에 parent_id 가 들어 있다).

재시작 뒤 키는 Redis 에 남고 run 채널(registry)은 사라진다 — 새로고침하면 새 키.
"""

import asyncio
import json
import logging
from uuid import UUID

from redis import Redis, RedisError

from app.api.errors import ApiError
from app.infra.redis import get_redis

log = logging.getLogger(__name__)

_TTL_SECONDS = 86400
"""24시간. 프로세스 메모리 시절에는 서버 재시작까지 무한이었으나
Redis 전환과 함께 자연 만료를 건다."""

# --- 프로세스 메모리 폴백 (REDIS_URL 없을 때) ---
_seen: dict[tuple[UUID, str, str, str], str | dict] = {}

_forgotten: set[str] = set()
"""Redis 장애로 Redis 에서 못 지운 실패 run. Redis 가 돌아와 그 값이 나와도 재생하지 않는다
(#671 리뷰). 안 그러면 같은 키 재시도가 24시간 TTL 동안 끝난 실패만 돌려받는다."""
# ponytail: 이 프로세스에서 실패한 run 만 가린다 — 서버를 늘리면 실패 표시를 Redis 밖(DB)에 둔다


_PROBE_KEY = "idem-probe"
"""recall 이 처음 보는 키에서 쓰기가 되는지 볼 때 쓰는 키. 값은 의미 없다."""


def _idem_key(parent_id: UUID, method: str, path: str, key: str) -> str:
    """Redis 키. idem:{parent_id}:{method}:{path}:{key}"""
    return f"idem:{parent_id}:{method}:{path}:{key}"


def _run_key(run_id: str) -> str:
    """forget_run 역인덱스 키. idem-run:{run_id} → 원래 idempotency 키."""
    return f"idem-run:{run_id}"


async def recall(*, parent_id: UUID, method: str, path: str, key: str) -> str | dict | None:
    """이 보호자가 이 창구에 이 키로 보낸 적이 있으면 그때의 재생값(run_id str 또는 응답 dict).

    Redis 장애면 503 — 아무것도 처리하지 않고 막는다 (모듈 설명). 처음 보는 키면 쓰기도 되는지
    먼저 본다 — RDB 저장에 실패한 Redis 는 읽기는 받고 쓰기만 MISCONF 로 거절해서, 읽기만 보고
    통과시키면 저장 · commit 뒤 remember 가 키를 못 남긴다 (#729 리뷰).
    동기 클라이언트라 이벤트 루프 밖(스레드)에서 부른다 — Redis 가 멈춘 동안 다른 요청 · SSE 가
    같이 멈추지 않게.
    """
    r = get_redis()
    if r is None:
        return _seen.get((parent_id, method, path, key))
    try:
        raw = await asyncio.to_thread(_read, r, _idem_key(parent_id, method, path, key))
    except RedisError as exc:
        _warn(exc, "요청을 503 으로 막는다")
        raise _unavailable() from None
    if raw is None:
        return None
    try:
        replay = json.loads(raw)
    except json.JSONDecodeError:
        # 깨진 값을 두면 TTL 24시간 동안 같은 키 재시도가 매번 막힌다 (ISSUE-F7). 지우고 이번만
        # 503 — 다음 재시도는 새 요청이 된다. 바로 새 요청으로 받지 않는 이유: 처음 요청이 저장까지
        # 끝냈는지 몰라 두 번 저장될 수 있다. 로그에는 창구만 — 키에는 parent_id 가 있다.
        log.warning("idempotency 재생 값이 깨져 지웠다 — 이번 요청은 503 %s %s", method, path)
        try:
            await asyncio.to_thread(r.delete, _idem_key(parent_id, method, path, key))
        except RedisError as exc:
            _warn(exc, "깨진 재생 값을 못 지웠다")
        raise _unavailable() from None
    # dict(일정 응답)는 forget_run 대상이 아니고 set 에 넣을 수도 없다
    return None if isinstance(replay, str) and replay in _forgotten else replay


def _read(r: Redis, idem: str) -> str | None:
    raw = r.get(idem)
    if raw is None:
        r.set(_PROBE_KEY, "1", ex=60)  # 쓰기가 막혔으면 여기서 RedisError(MISCONF)
    return raw


async def remember(
    *, parent_id: UUID, method: str, path: str, key: str, replay: str | dict, strict: bool = False
) -> None:
    """재생값을 기억한다. 한 줄 입력은 run_id(str), 일정 제출은 응답 전체(dict).

    Redis 장애여도 기본은 예외를 내지 않는다 — 저장(commit)이 끝난 뒤라 실패로 답하면 보호자가 다시
    눌러 겹친다. 키는 못 남긴다: recall 과 이 사이(핸들러 전체 — DB 저장 · commit 포함)에
    Redis 가 죽은 요청을 Redis 가 돌아온 뒤 같은 키로 다시 보내면 새 요청이 된다
    (docs/api/idempotency-v1.md §3-6).

    `strict` — 아직 아무것도 저장하지 않은 창구(한 줄 입력은 러너보다 먼저 적는다)는 503 으로
    끝낸다.
    """
    r = get_redis()
    if r is None:
        _seen[(parent_id, method, path, key)] = replay
        return
    try:
        await asyncio.to_thread(_write, r, _idem_key(parent_id, method, path, key), replay)
    except RedisError as exc:
        _warn(exc, "키를 남기지 못했다")
        if strict:
            # 적고 나서 응답만 늦은 것일 수 있다 — 같은 키 재시도가 처리 안 된 이 run 을 재생하지
            # 않게 버린 run 으로 둔다 (#729 리뷰)
            if isinstance(replay, str):
                _forgotten.add(replay)
            raise _unavailable() from None


def _write(r: Redis, idem: str, replay: str | dict) -> None:
    r.set(idem, json.dumps(replay), ex=_TTL_SECONDS)
    # 역인덱스: run_id(str)일 때만. dict(일정 응답)는 forget_run 대상이 아니다.
    if isinstance(replay, str):
        r.set(_run_key(replay), idem, ex=_TTL_SECONDS)


def _unavailable() -> ApiError:
    return ApiError(503, "temporarily_unavailable", "잠시 후 다시 시도해 주세요")


def forget_run(run_id: str) -> None:
    """이 run 을 가리키는 키를 지운다. 실패로 끝난 run 에 쓴다.

    기억하는 것은 성공뿐이다 (§3-4). 실패한 run 이 남아 있으면 같은 키로 "다시 시도" 할 때마다
    이미 닫힌 실패가 재생돼 재시도가 영원히 막힌다.
    """
    r = get_redis()
    if r is None:
        for scope in [scope for scope, seen in _seen.items() if seen == run_id]:
            del _seen[scope]
        return

    rk = _run_key(run_id)
    try:
        idem = r.get(rk)
        if idem is not None:
            r.delete(idem, rk)
    except RedisError as exc:
        _warn(exc, "실패한 run 의 키를 못 지웠다")
        _forgotten.add(run_id)  # Redis 의 키는 24시간 TTL 로 사라진다. 그때까지 recall 이 가린다


def _warn(exc: RedisError, what: str) -> None:
    log.warning("Redis 장애 — idempotency %s: %s", what, type(exc).__name__)


def clear() -> None:
    """idem:* · idem-run:* 키와 메모리 폴백을 비운다. 테스트용."""
    _seen.clear()
    _forgotten.clear()
    r = get_redis()
    if r is None:
        return

    try:
        for pattern in ("idem:*", "idem-run:*"):
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=pattern, count=200)
                if keys:
                    r.delete(*keys)
                if cursor == 0:
                    break
    except RedisError as exc:  # 장애를 흉내 낸 테스트의 뒷정리
        _warn(exc, "키를 못 비웠다")
