"""F7 (P1) — Redis 에 깨진 재생 값이 있을 때 (eval-v1 §6 · `idempotency.py:83`).

`recall` 은 Redis 값을 `json.loads` 로 바로 읽는다. 값이 깨져 있으면 `JSONDecodeError` 가
그대로 올라가 마지막 그물(`errors.py` 의 `_unhandled`)이 500 을 낸다. 키는 24시간 TTL 이라
그동안 같은 키 재시도가 매번 500 이었다. 지금은 깨진 키를 지우고 503 으로 답한다.
테스트는 "처리 안 된 예외가 아니다" 까지만 본다. 지우는 것은 test_idempotency_redis.py 가 본다.
"""

from unittest.mock import patch
from uuid import UUID

import fakeredis

from app.api import idempotency
from app.api.errors import ApiError

PID = UUID(int=7)
SCOPE = {"parent_id": PID, "method": "POST", "path": "/children/c/inputs", "key": "f7"}


async def test_F7_깨진_재생_값은_처리_안_된_예외가_아니다() -> None:
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set(idempotency._idem_key(**SCOPE), "{깨진")
    with patch("app.api.idempotency.get_redis", return_value=r):
        try:
            replay = await idempotency.recall(**SCOPE)
        except ApiError as exc:
            assert exc.status != 500  # 정해진 오류(예: 503)로 답하면 된다
        else:
            assert replay is None or isinstance(replay, str | dict)
