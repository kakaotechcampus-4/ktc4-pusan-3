"""idempotency Redis 모드 테스트 — fakeredis 로 실제 Redis 없이 돈다."""

from unittest.mock import patch
from uuid import uuid4

import fakeredis

from app.api import idempotency

PID = uuid4()


def _fake_redis():
    """테스트 전체가 공유하는 fakeredis 인스턴스."""
    return fakeredis.FakeRedis(decode_responses=True)


_r = _fake_redis()


@patch("app.api.idempotency.get_redis", return_value=_r)
class TestIdempotencyRedis:
    def setup_method(self):
        _r.flushdb()

    def test_remember_recall_str(self, _mock):
        """run_id(str) 저장 → 같은 스코프로 recall 하면 돌아온다."""
        idempotency.remember(parent_id=PID, method="POST", path="/input", key="k1", replay="run-1")
        result = idempotency.recall(parent_id=PID, method="POST", path="/input", key="k1")
        assert result == "run-1"

    def test_remember_recall_dict(self, _mock):
        """dict 저장 → recall 결과가 원본과 같다."""
        body = {"id": "sug-1", "status": "approved"}
        idempotency.remember(parent_id=PID, method="POST", path="/suggest", key="k2", replay=body)
        result = idempotency.recall(parent_id=PID, method="POST", path="/suggest", key="k2")
        assert result == body

    def test_recall_miss(self, _mock):
        """저장한 적 없는 키 → None."""
        assert idempotency.recall(parent_id=PID, method="POST", path="/x", key="nope") is None

    def test_forget_run_removes_both_keys(self, _mock):
        """forget_run → idempotency 키와 역인덱스 키 모두 삭제된다."""
        idempotency.remember(parent_id=PID, method="POST", path="/input", key="k3", replay="run-2")
        idempotency.forget_run("run-2")
        assert idempotency.recall(parent_id=PID, method="POST", path="/input", key="k3") is None
        assert _r.get("idem-run:run-2") is None

    def test_dict_no_reverse_index(self, _mock):
        """dict replay 는 역인덱스를 만들지 않는다."""
        body = {"ok": True}
        idempotency.remember(parent_id=PID, method="POST", path="/s", key="k4", replay=body)
        # dict 는 str 이 아니므로 idem-run 키가 없어야 한다
        keys = _r.keys("idem-run:*")
        assert keys == []

    def test_clear_only_idem_keys(self, _mock):
        """clear() 는 idem:* · idem-run:* 만 지우고 다른 키는 남긴다."""
        idempotency.remember(parent_id=PID, method="POST", path="/input", key="k5", replay="run-3")
        _r.set("other:something", "keep-me")

        idempotency.clear()

        assert idempotency.recall(parent_id=PID, method="POST", path="/input", key="k5") is None
        assert _r.get("other:something") == "keep-me"
