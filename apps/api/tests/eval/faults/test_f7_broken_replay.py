"""F7 (P1) — Redis 에 깨진 재생 값이 있을 때 (eval-v1 §6 · `idempotency.py:83`).

`recall` 은 Redis 값을 `json.loads` 로 바로 읽는다. 값이 깨져 있으면 `JSONDecodeError` 가
그대로 올라가 마지막 그물(`errors.py` 의 `_unhandled`)이 500 을 낸다. 키는 24시간 TTL 이라
그동안 같은 키 재시도가 매번 500 이다. 어떻게 답할지(503 · 새 요청으로 처리)는 아직 정하지
않았다 — 테스트는 "처리 안 된 예외가 아니다" 까지만 본다.
"""

import json
from unittest.mock import patch
from uuid import UUID

import fakeredis
import pytest

from app.api import idempotency
from app.api.errors import ApiError

PID = UUID(int=7)
SCOPE = {"parent_id": PID, "method": "POST", "path": "/children/c/inputs", "key": "f7"}


@pytest.mark.xfail(
    strict=True,
    raises=json.JSONDecodeError,
    reason="ISSUE-F7 (eval/faults-issues.md) — 깨진 재생 값이 JSONDecodeError → 500",
)
async def test_F7_깨진_재생_값은_처리_안_된_예외가_아니다() -> None:
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set(idempotency._idem_key(**SCOPE), "{깨진")
    with patch("app.api.idempotency.get_redis", return_value=r):
        try:
            replay = await idempotency.recall(**SCOPE)
        # ISSUE-F7 을 고친 뒤에야 이 갈래를 탄다 — 그 전에는 strict xfail 이 JSONDecodeError 기대
        except ApiError as exc:
            assert exc.status != 500  # 정해진 오류(예: 503)로 답하면 된다
        else:
            assert replay is None or isinstance(replay, str | dict)
