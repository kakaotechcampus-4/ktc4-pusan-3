"""Idempotency-Key 기억 — Redis 또는 프로세스 메모리 폴백.

계약서 §01 은 "되돌릴 수 없는 POST 는 헤더가 없으면 400" 까지만 정한다.
docs/api/idempotency-v1.md 가 그 위에 제안한 동작 중 **"같은 키 · 같은 보호자 → 처음 응답 재생"**
까지만 여기서 한다.
프론트 mutation 은 retry: false 라 중복은 보호자가 버튼을 다시 누를 때만 생기고, 그때 재생이
없으면 같은 한 줄이 관찰 N건씩 두 번 저장된다.

- 스코프는 (parent_id, method, path, key) — 보호자 사이에 키가 섞이지 않는다 (§3-5)
- 422(같은 키 · 다른 본문) · 409(처리 중) · 24시간 보관은 다음 이슈. 409 는 접수가 즉시 끝나는
  구조라 창이 몇 ms 뿐이고, 키를 **러너를 띄우기 전에** 적어 두면 그 창도 재생으로 흡수된다

REDIS_URL 이 설정되면 Redis 를 쓰고, 없으면 프로세스 메모리 dict 로 폴백한다.
폴백은 단일 워커 전제다 (#134).
"""

import json
from uuid import UUID

from app.infra.redis import get_redis

_TTL_SECONDS = 86400
"""24시간. 프로세스 메모리 시절에는 서버 재시작까지 무한이었으나
Redis 전환과 함께 자연 만료를 건다."""

# --- 프로세스 메모리 폴백 (REDIS_URL 없을 때) ---
_seen: dict[tuple[UUID, str, str, str], str | dict] = {}


def _idem_key(parent_id: UUID, method: str, path: str, key: str) -> str:
    """Redis 키. idem:{parent_id}:{method}:{path}:{key}"""
    return f"idem:{parent_id}:{method}:{path}:{key}"


def _run_key(run_id: str) -> str:
    """forget_run 역인덱스 키. idem-run:{run_id} → 원래 idempotency 키."""
    return f"idem-run:{run_id}"


def recall(*, parent_id: UUID, method: str, path: str, key: str) -> str | dict | None:
    """이 보호자가 이 창구에 이 키로 보낸 적이 있으면 그때의 재생값(run_id str 또는 응답 dict)."""
    r = get_redis()
    if r is None:
        return _seen.get((parent_id, method, path, key))

    raw = r.get(_idem_key(parent_id, method, path, key))
    if raw is None:
        return None
    return json.loads(raw)


def remember(*, parent_id: UUID, method: str, path: str, key: str, replay: str | dict) -> None:
    """재생값을 기억한다. 한 줄 입력은 run_id(str), 일정 제출은 응답 전체(dict)."""
    r = get_redis()
    if r is None:
        _seen[(parent_id, method, path, key)] = replay
        return

    idem = _idem_key(parent_id, method, path, key)
    pipe = r.pipeline()
    pipe.set(idem, json.dumps(replay), ex=_TTL_SECONDS)
    # 역인덱스: run_id(str)일 때만. dict(일정 응답)는 forget_run 대상이 아니다.
    if isinstance(replay, str):
        pipe.set(_run_key(replay), idem, ex=_TTL_SECONDS)
    pipe.execute()


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
    idem = r.get(rk)
    if idem is not None:
        r.delete(idem, rk)


def clear() -> None:
    """전부 비운다. 테스트용."""
    r = get_redis()
    if r is None:
        _seen.clear()
        return

    r.flushdb()
