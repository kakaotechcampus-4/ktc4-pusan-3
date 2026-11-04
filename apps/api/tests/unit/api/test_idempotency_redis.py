"""idempotency Redis 모드 테스트 — fakeredis 로 실제 Redis 없이 돈다."""

from unittest.mock import patch
from uuid import uuid4

import fakeredis
import redis

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


# ---------------------------------------------------------------------------
# Redis 장애 (#671) — 입력 창구가 500 이 되지 않게 메모리로 계속 받는다
# ---------------------------------------------------------------------------

_down = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
"""아무것도 듣지 않는 포트 — 부르면 진짜 ConnectionError 가 난다."""


class TestRedisDown:
    def setup_method(self):
        _r.flushdb()
        idempotency.clear()

    def test_장애_중에도_기억하고_재생한다(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d1", replay="run-d")
            assert idempotency.recall(parent_id=PID, method="POST", path="/i", key="d1") == "run-d"

    def test_장애_중_적은_키는_Redis_가_돌아온_뒤에도_재생된다(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d2", replay="run-e")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert idempotency.recall(parent_id=PID, method="POST", path="/i", key="d2") == "run-e"

    def test_장애_중_적은_키도_실패한_run_이면_지운다(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d3", replay="run-f")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            idempotency.forget_run("run-f")
            assert idempotency.recall(parent_id=PID, method="POST", path="/i", key="d3") is None

    def test_장애_중_forget_run_은_터지지_않는다(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.remember(parent_id=PID, method="POST", path="/i", key="d4", replay="run-g")
            idempotency.forget_run("run-g")
            assert idempotency.recall(parent_id=PID, method="POST", path="/i", key="d4") is None

    def test_로그에는_오류_종류만_남는다(self, caplog):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.recall(parent_id=PID, method="POST", path="/i", key="d5")
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


def test_Redis_가_응답_없이_매달려도_1초_남짓에_메모리로_넘어간다(monkeypatch):
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
        result = idempotency.recall(parent_id=PID, method="POST", path="/h", key="hang")
        elapsed = time.monotonic() - started
    finally:
        server.close()
        for conn in held:
            conn.close()

    assert result is None
    assert elapsed < infra_redis.TIMEOUT_S * 3


def test_strict_는_Redis_장애면_메모리로_넘어가지_않고_503():
    """승인 게이트 쓰기 — 장애 직전 Redis 키가 안 보여 두 번 저장되는 것을 막는다 (#671 리뷰)."""
    import pytest

    from app.api.errors import ApiError

    with patch("app.api.idempotency.get_redis", return_value=_down):
        with pytest.raises(ApiError) as caught:
            idempotency.recall(parent_id=PID, method="POST", path="/e", key="s1", strict=True)
    assert (caught.value.status, caught.value.code) == (503, "temporarily_unavailable")
