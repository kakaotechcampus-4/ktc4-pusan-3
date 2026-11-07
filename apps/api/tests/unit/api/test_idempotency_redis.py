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

    async def test_remember_recall_str(self, _mock):
        """run_id(str) 저장 → 같은 스코프로 recall 하면 돌아온다."""
        await idempotency.remember(
            parent_id=PID, method="POST", path="/input", key="k1", replay="run-1"
        )
        result = await idempotency.recall(parent_id=PID, method="POST", path="/input", key="k1")
        assert result == "run-1"

    async def test_remember_recall_dict(self, _mock):
        """dict 저장 → recall 결과가 원본과 같다."""
        body = {"id": "sug-1", "status": "approved"}
        await idempotency.remember(
            parent_id=PID, method="POST", path="/suggest", key="k2", replay=body
        )
        result = await idempotency.recall(parent_id=PID, method="POST", path="/suggest", key="k2")
        assert result == body

    async def test_recall_miss(self, _mock):
        """저장한 적 없는 키 → None."""
        assert await idempotency.recall(parent_id=PID, method="POST", path="/x", key="nope") is None

    async def test_forget_run_removes_both_keys(self, _mock):
        """forget_run → idempotency 키와 역인덱스 키 모두 삭제된다."""
        await idempotency.remember(
            parent_id=PID, method="POST", path="/input", key="k3", replay="run-2"
        )
        idempotency.forget_run("run-2")
        assert (
            await idempotency.recall(parent_id=PID, method="POST", path="/input", key="k3") is None
        )
        assert _r.get("idem-run:run-2") is None

    async def test_dict_no_reverse_index(self, _mock):
        """dict replay 는 역인덱스를 만들지 않는다."""
        body = {"ok": True}
        await idempotency.remember(parent_id=PID, method="POST", path="/s", key="k4", replay=body)
        # dict 는 str 이 아니므로 idem-run 키가 없어야 한다
        keys = _r.keys("idem-run:*")
        assert keys == []

    async def test_clear_only_idem_keys(self, _mock):
        """clear() 는 idem:* · idem-run:* 만 지우고 다른 키는 남긴다."""
        await idempotency.remember(
            parent_id=PID, method="POST", path="/input", key="k5", replay="run-3"
        )
        _r.set("other:something", "keep-me")

        idempotency.clear()

        assert (
            await idempotency.recall(parent_id=PID, method="POST", path="/input", key="k5") is None
        )
        assert _r.get("other:something") == "keep-me"


# ---------------------------------------------------------------------------
# Redis 장애 (#671) — 메모리로 넘어가지 않고 503 으로 막는다. 다시 보내면 되는 오류다
# ---------------------------------------------------------------------------

_down = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
"""아무것도 듣지 않는 포트 — 부르면 진짜 ConnectionError 가 난다."""


class WriteRefused(fakeredis.FakeRedis):
    """RDB 저장에 실패한 Redis — 기본 설정상 읽기는 받고 쓰기만 MISCONF 로 거절한다 (#671 리뷰)."""

    def execute_command(self, *args, **options):
        if str(args[0]).upper() in {"SET", "SETEX", "DEL"}:
            raise redis.ResponseError("MISCONF Redis is configured to save RDB snapshots")
        return super().execute_command(*args, **options)


async def _refused(**scope) -> ApiError:
    with pytest.raises(ApiError) as caught:
        await idempotency.recall(parent_id=PID, method="POST", **scope)
    return caught.value


class TestRedisDown:
    def setup_method(self):
        _r.flushdb()
        idempotency.clear()

    async def test_장애면_recall_은_503(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            refused = await _refused(path="/i", key="d1")
        assert (refused.status, refused.code) == (503, "temporarily_unavailable")

    async def test_장애_직전에_적힌_키를_새_요청으로_받지_않는다(self):
        """메모리로 넘어가면 이 키가 안 보여 같은 요청이 두 번 저장된다 — 그래서 막는다."""
        with patch("app.api.idempotency.get_redis", return_value=_r):
            await idempotency.remember(
                parent_id=PID, method="POST", path="/i", key="d2", replay="run-a"
            )
        with patch("app.api.idempotency.get_redis", return_value=_down):
            await _refused(path="/i", key="d2")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert (
                await idempotency.recall(parent_id=PID, method="POST", path="/i", key="d2")
                == "run-a"
            )

    async def test_장애_중_remember_는_터지지_않고_메모리에도_적지_않는다(self):
        """저장(commit) 뒤라 실패로 답하면 보호자가 다시 눌러 겹친다. 메모리 상태는 두지 않는다."""
        with patch("app.api.idempotency.get_redis", return_value=_down):
            await idempotency.remember(
                parent_id=PID, method="POST", path="/i", key="d3", replay="run-b"
            )
        assert idempotency._seen == {}

    async def test_쓰기만_거절되면_처리하기_전에_503(self):
        """GET 만 보고 통과시키면 저장 · commit 뒤 키를 못 남겨 같은 키 재시도가 두 번 저장된다."""
        with patch("app.api.idempotency.get_redis", return_value=WriteRefused()):
            refused = await _refused(path="/i", key="misconf")
        assert (refused.status, refused.code) == (503, "temporarily_unavailable")

    async def test_장애_중_forget_run_은_터지지_않는다(self):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.forget_run("run-c")

    async def test_로그에는_오류_종류만_남는다(self, caplog):
        with patch("app.api.idempotency.get_redis", return_value=_down):
            await _refused(path="/i", key="d5")
        assert "ConnectionError" in caplog.text
        assert str(PID) not in caplog.text

    async def test_장애_중_못_지운_실패_run_은_복구_뒤에도_재생하지_않는다(self):
        """Redis 에 남은 실패 run 이 TTL(24시간) 동안 같은 키 재시도를 막지 않게 (#671 리뷰)."""
        with patch("app.api.idempotency.get_redis", return_value=_r):
            await idempotency.remember(
                parent_id=PID, method="POST", path="/i", key="d6", replay="run-x"
            )
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.forget_run("run-x")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert (
                await idempotency.recall(parent_id=PID, method="POST", path="/i", key="d6") is None
            )

    async def test_dict_재생값은_못_지운_run_목록과_상관없이_재생된다(self):
        body = {"id": "evt-1"}
        with patch("app.api.idempotency.get_redis", return_value=_r):
            await idempotency.remember(
                parent_id=PID, method="POST", path="/e", key="d7", replay=body
            )
        with patch("app.api.idempotency.get_redis", return_value=_down):
            idempotency.forget_run("run-y")
        with patch("app.api.idempotency.get_redis", return_value=_r):
            assert (
                await idempotency.recall(parent_id=PID, method="POST", path="/e", key="d7") == body
            )


@pytest.fixture
def hung(monkeypatch, hung_redis_url):
    """get_redis() 가 응답 없는 서버를 가리키게 한다 — 실제 설정(상한 · 차단기)을 그대로 탄다."""
    from app.infra import redis as infra_redis

    monkeypatch.setattr(infra_redis.settings, "REDIS_URL", hung_redis_url)
    monkeypatch.setattr(infra_redis, "_client", None)
    return infra_redis


async def test_Redis_가_응답_없이_매달려도_1초_남짓에_503(hung):
    """연결은 받고 아무 말도 안 하는 서버 — 연결 거부와 달리 응답 상한이 있어야 끝난다."""
    import time

    started = time.monotonic()
    refused = await _refused(path="/h", key="hang")
    elapsed = time.monotonic() - started

    assert refused.status == 503
    assert elapsed < hung.TIMEOUT_S * 3


@pytest.mark.parametrize("op", ["recall", "remember"])
async def test_Redis_가_매달려도_이벤트_루프는_멈추지_않는다(hung, op):
    """동기 클라이언트를 루프에서 부르면 기다리는 동안 다른 요청 · SSE 가 다 멈춘다 (#671 리뷰)."""
    import asyncio
    import time

    gaps: list[float] = []

    async def ticker() -> None:
        last = time.monotonic()
        for _ in range(int(hung.TIMEOUT_S / 0.05) + 5):
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    async def call() -> None:
        if op == "remember":  # 기본은 오류를 삼킨다
            await idempotency.remember(
                parent_id=PID, method="POST", path="/h", key="loop", replay="run-x"
            )
            return
        with pytest.raises(ApiError):
            await idempotency.recall(parent_id=PID, method="POST", path="/h", key="loop")

    await asyncio.gather(ticker(), call())

    assert max(gaps) < 0.3


async def test_Redis_가_멈춘_동안_여러_번_불러도_한_번만_기다린다(hung):
    """차단기 — 한 번 시간 초과가 나면 잠시 Redis 를 부르지 않고 바로 503 (#671 리뷰).

    안 그러면 요청마다 상한만큼 기다려 스레드가 쌓인다.
    """
    import time

    started = time.monotonic()
    for i in range(5):
        assert (await _refused(path="/h", key=f"burst-{i}")).status == 503
    assert time.monotonic() - started < hung.TIMEOUT_S * 2


def test_새_연결의_첫_호출도_응답_한_번만_기다린다(monkeypatch):
    """redis-py 는 새 연결에서 HELLO · CLIENT SETINFO 를 먼저 보낸다 — 느린 Redis 에선 왕복마다
    기다려 1초 상한이 몇 배로 늘어난다 (#671 리뷰)."""
    import socket
    import threading
    import time

    from fakeredis import TcpFakeServer

    from app.infra import redis as infra_redis

    delay = 0.3
    backend = TcpFakeServer(("127.0.0.1", 0), server_type="redis")
    threading.Thread(target=backend.serve_forever, daemon=True).start()

    def pipe(src: socket.socket, dst: socket.socket, wait: float) -> None:
        try:
            while data := src.recv(65536):
                time.sleep(wait)
                dst.sendall(data)
        except OSError:
            pass

    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen()

    def accept() -> None:
        while True:
            try:
                client = proxy.accept()[0]
            except OSError:
                return
            upstream = socket.create_connection(backend.server_address)
            threading.Thread(target=pipe, args=(client, upstream, 0), daemon=True).start()
            threading.Thread(target=pipe, args=(upstream, client, delay), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    monkeypatch.setattr(
        infra_redis.settings, "REDIS_URL", f"redis://127.0.0.1:{proxy.getsockname()[1]}/0"
    )
    monkeypatch.setattr(infra_redis, "_client", None)
    try:
        started = time.monotonic()
        assert infra_redis.get_redis().get("x") is None
        elapsed = time.monotonic() - started
    finally:
        proxy.close()
        backend.shutdown()
        backend.server_close()

    assert elapsed < delay * 2


def test_Idempotency_Key_를_받는_창구는_모두_Swagger_에_503_이_있다():
    """키를 받는 창구는 recall 을 불러 Redis 장애면 503 이다. 창구가 늘어도 Swagger 에 남게."""
    from app.main import app

    windows = {
        (method.upper(), path): "503" in op["responses"]
        for path, ops in app.openapi()["paths"].items()
        for method, op in ops.items()
        if any(p["name"].lower() == "idempotency-key" for p in op.get("parameters", []))
    }
    assert len(windows) == 10
    assert [w for w, ok in windows.items() if not ok] == []
