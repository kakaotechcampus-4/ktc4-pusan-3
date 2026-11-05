"""idempotency Redis 모드 테스트 — fakeredis 로 실제 Redis 없이 돈다."""

from unittest.mock import patch
from uuid import uuid4

import fakeredis
import pytest
import redis

from app.api import idempotency
from app.api.errors import ApiError

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


# ---------------------------------------------------------------------------
# Redis 장애 (#671) — 메모리로 넘어가지 않고 503 으로 막는다. 다시 보내면 되는 오류다
# ---------------------------------------------------------------------------

_down = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
"""아무것도 듣지 않는 포트 — 부르면 진짜 ConnectionError 가 난다."""


def _refused(**scope) -> ApiError:
    with pytest.raises(ApiError) as caught:
        idempotency.recall(parent_id=PID, method="POST", **scope)
    return caught.value


class TestRedisDown:
    def setup_method(self):
        _r.flushdb()
        idempotency.clear()

    def test_장애면_recall_은_503(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            refused = _refused(path="/i", key="d1")
        assert (refused.status, refused.code) == (503, "temporarily_unavailable")

    def test_장애_직전에_적힌_키를_새_요청으로_받지_않는다(self):
        """메모리로 넘어가면 이 키가 안 보여 같은 요청이 두 번 저장된다 — 그래서 막는다."""
        with patch("app.api.idempotency.get_redis", return_value=_r):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d2", replay="run-a")
        with patch("app.api.idempotency.get_redis", return_value=_down):
            _refused(path="/i", key="d2")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert idempotency.recall(parent_id=PID, method="POST", path="/i", key="d2") == "run-a"

    def test_장애_중_remember_는_터지지_않고_메모리에도_적지_않는다(self):
        """저장(commit) 뒤라 실패로 답하면 보호자가 다시 눌러 겹친다. 메모리 상태는 두지 않는다."""
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d3", replay="run-b")
        assert idempotency._seen == {}

    def test_장애_중_forget_run_은_터지지_않는다(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.forget_run("run-c")

    def test_로그에는_오류_종류만_남는다(self, caplog):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            _refused(path="/i", key="d5")
        assert "ConnectionError" in caplog.text
        assert str(PID) not in caplog.text

    def test_장애_중_못_지운_실패_run_은_복구_뒤에도_재생하지_않는다(self):
        """Redis 에 남은 실패 run 이 TTL(24시간) 동안 같은 키 재시도를 막지 않게 (#671 리뷰)."""
        with patch("app.api.idempotency.get_redis", return_value=_r):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d6", replay="run-x")
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.forget_run("run-x")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert idempotency.recall(parent_id=PID, method="POST", path="/i", key="d6") is None

    def test_dict_재생값은_못_지운_run_목록과_상관없이_재생된다(self):
        body = {"id": "evt-1"}
        with patch("app.api.idempotency.get_redis", return_value=_r):
            idempotency.remember(parent_id=PID, method="POST", path="/e", key="d7", replay=body)
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.forget_run("run-y")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert idempotency.recall(parent_id=PID, method="POST", path="/e", key="d7") == body


def test_Redis_가_응답_없이_매달려도_1초_남짓에_503(monkeypatch):
    """연결은 받고 아무 말도 안 하는 서버 — 연결 거부(즉시 실패)와 달리 응답 상한이 있어야 끝난다.

    동기 클라이언트라 그동안 이벤트 루프 전체가 멈춘다 (#671 리뷰).
    """
    import socket
    import threading
    import time

    from app.infra import redis as infra_redis

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held: list[socket.socket] = []
    threading.Thread(target=lambda: held.append(server.accept()[0]), daemon=True).start()
    monkeypatch.setattr(
        infra_redis.settings, "REDIS_URL", f"redis://127.0.0.1:{server.getsockname()[1]}/0"
    )
    monkeypatch.setattr(infra_redis, "_client", None)
    try:
        started = time.monotonic()
        refused = _refused(path="/h", key="hang")
        elapsed = time.monotonic() - started
    finally:
        server.close()
        for conn in held:
            conn.close()

    assert refused.status == 503
    assert elapsed < infra_redis.TIMEOUT_S * 3
