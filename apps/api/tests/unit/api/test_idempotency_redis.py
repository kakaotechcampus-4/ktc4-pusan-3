"""idempotency Redis 모드 테스트 — fakeredis 로 실제 Redis 없이 돈다."""

import asyncio
import json
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

    @pytest.mark.parametrize("broken", ["{깨진", "123", "[1, 2]", "null"])
    async def test_깨진_재생값은_이번만_503_이고_지워져_다음은_새_요청(self, _mock, caplog, broken):
        """#935 — 못 읽거나 모양이 틀린 값이 24시간 남아 재시도를 매번 막지 않는다."""
        scope = {"parent_id": PID, "method": "POST", "path": "/x", "key": "broken"}
        _r.set(idempotency._idem_key(**scope), broken)
        with pytest.raises(ApiError) as caught:
            await idempotency.recall(**scope)
        assert caught.value.status == 503
        assert "깨진 재생 값을 지웠다 — 다음 재시도는 새 요청 POST /x" in caplog.text
        assert str(PID) not in caplog.text
        assert await idempotency.recall(**scope) is None

    async def test_깨진_값을_못_지우면_지웠다고_남기지_않는다(self, _mock, caplog):
        scope = {"parent_id": PID, "method": "POST", "path": "/x", "key": "broken"}
        _r.set(idempotency._idem_key(**scope), "{깨진")
        with patch.object(_r, "delete", side_effect=redis.ConnectionError()):
            with pytest.raises(ApiError):
                await idempotency.recall(**scope)
        assert "못 지웠다" in caplog.text
        assert "지웠다 — 다음 재시도" not in caplog.text

    async def test_옛_run_의_늦은_forget_run_은_같은_키의_새_run_을_지우지_않는다(self, _mock):
        """#935 리뷰 — 깨진 값을 지운 뒤 같은 키에 새 run 이 적혔으면 옛 역인덱스는 남의 키다."""
        scope = {"parent_id": PID, "method": "POST", "path": "/x", "key": "reuse"}
        await idempotency.remember(**scope, replay="old")
        _r.set(idempotency._idem_key(**scope), "{깨진")
        with pytest.raises(ApiError):
            await idempotency.recall(**scope)
        await idempotency.remember(**scope, replay="new")
        idempotency.forget_run("old")
        assert await idempotency.recall(**scope) == "new"
        assert _r.get(idempotency._run_key("old")) is None

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

    async def test_commit_이_실패하면_적은_키를_지운다(self, _mock):
        """키는 commit 전에 적는다 (#774). commit 이 실패하면 지운 것이 없으니 같은 키 재시도가
        성공을 재생받지 않고 다시 지우러 가야 한다."""

        scope = {"parent_id": PID, "method": "DELETE", "path": "/o", "key": "c1"}

        class BrokenCommit:
            async def commit(self):
                assert await idempotency.recall(**scope) is not None  # 키가 commit 보다 먼저다
                raise RuntimeError("commit failed")

        with pytest.raises(RuntimeError):
            await idempotency.remember_and_commit(
                BrokenCommit(), **scope, replay={"status_code": 204}
            )

        assert await idempotency.recall(**scope) is None

    async def test_키_적기가_시간_초과로_503_이어도_적힌_키를_지운다(self, _mock):
        """Redis 가 SET 은 받고 응답만 늦으면 503 인데 키는 적혀 있다 (#916 리뷰). 남으면 같은 키
        재시도가 지우지 않은 삭제의 성공을 재생받는다."""

        scope = {"parent_id": PID, "method": "DELETE", "path": "/o", "key": "slow"}
        real_set = _r.set

        def set_then_time_out(*args, **kwargs):
            real_set(*args, **kwargs)
            raise redis.TimeoutError("Timeout reading from socket")

        class NoCommit:
            async def commit(self):
                raise AssertionError("키 적기가 실패했는데 commit 했다")

        with patch.object(_r, "set", side_effect=set_then_time_out):
            with pytest.raises(ApiError) as caught:
                await idempotency.remember_and_commit(
                    NoCommit(), **scope, replay={"status_code": 204}
                )

        assert caught.value.status == 503
        assert await idempotency.recall(**scope) is None

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


# ---------------------------------------------------------------------------
# remember_first — 키를 "처음 적은 요청만" 통과시킨다 (SET NX)
# recall(GET) 과 remember(SET) 사이에 온 같은 키는 둘 다 "처음" 을 봐서 run 이 둘 떴다
# ---------------------------------------------------------------------------

_SCOPE = {"parent_id": PID, "method": "POST", "path": "/i"}
_IDEM = idempotency._idem_key(PID, "POST", "/i", "nx")


async def _first(replay: str, key: str = "nx") -> str | None:
    return await idempotency.remember_first(**_SCOPE, key=key, replay=replay)


@patch("app.api.idempotency.get_redis", return_value=_r)
class TestRememberFirst:
    def setup_method(self):
        _r.flushdb()
        idempotency.clear()

    def teardown_method(self):
        idempotency.clear()

    async def test_처음이면_None_이고_키와_역인덱스를_24시간_적는다(self, _mock):
        assert await _first("run-1") is None

        assert json.loads(_r.get(_IDEM)) == "run-1"
        assert _r.get("idem-run:run-1") == _IDEM
        for key in (_IDEM, "idem-run:run-1"):
            assert 86400 - 5 < _r.ttl(key) <= 86400

    async def test_두_번째는_처음_run_을_돌려받고_값을_바꾸지_않는다(self, _mock):
        await _first("run-1")

        assert await _first("run-2") == "run-1"
        assert json.loads(_r.get(_IDEM)) == "run-1"
        assert _r.get("idem-run:run-2") is None  # 진 run 은 역인덱스도 남기지 않는다

    async def test_동시에_여덟_번_와도_하나만_처음이다(self, _mock):
        runs = [f"run-{i}" for i in range(8)]

        results = await asyncio.gather(*(_first(run) for run in runs))

        assert results.count(None) == 1
        winner = runs[results.index(None)]
        assert [r for r in results if r is not None] == [winner] * 7
        assert json.loads(_r.get(_IDEM)) == winner

    async def test_못_지운_실패_run_이_적혀_있으면_덮어쓴다(self, _mock):
        """recall 이 가린 실패 run 이다 — 그 run 을 돌려주면 같은 키 재시도가 끝난 실패만 받는다."""
        await _first("run-x")
        idempotency._forgotten.add("run-x")  # Redis 장애로 forget_run 이 못 지웠다

        assert await _first("run-y") is None
        assert json.loads(_r.get(_IDEM)) == "run-y"
        assert _r.get("idem-run:run-y") == _IDEM

    async def test_사이에_키가_사라지면_한_번_더_적어_본다(self, _mock):
        """NX 가 진 뒤 읽기 전에 키가 지워졌다(실패 run 의 forget_run) — 비었으니 내가 처음이다."""
        _r.set(_IDEM, json.dumps("run-old"))
        real_get = _r.get

        def vanishing_get(name):
            if name == _IDEM:
                _r.delete(_IDEM)
            return real_get(name)

        with patch.object(_r, "get", side_effect=vanishing_get):
            assert await _first("run-new") is None
        assert json.loads(_r.get(_IDEM)) == "run-new"

    @pytest.mark.parametrize("stored", ["not-json", json.dumps({"id": "evt-1"}), json.dumps(3)])
    async def test_run_id_가_아닌_값이면_503_이고_지우지_않는다(self, _mock, stored):
        _r.set(_IDEM, stored)

        with pytest.raises(ApiError) as caught:
            await _first("run-1")

        assert (caught.value.status, caught.value.code) == (503, "temporarily_unavailable")
        assert _r.get(_IDEM) == stored

    async def test_Redis_장애면_503_이고_내_run_은_버린_run_이_된다(self, _mock):
        """적고 나서 응답만 늦은 것일 수 있다 — 같은 키 재시도가 처리 안 된 이 run 을 받지 않게."""
        with patch("app.api.idempotency.get_redis", return_value=_down):
            with pytest.raises(ApiError) as caught:
                await _first("run-z")

        assert caught.value.status == 503
        assert "run-z" in idempotency._forgotten

    async def test_쓰기만_거절돼도_503(self, _mock):
        with patch("app.api.idempotency.get_redis", return_value=WriteRefused()):
            with pytest.raises(ApiError) as caught:
                await _first("run-m")

        assert caught.value.status == 503
        assert "run-m" in idempotency._forgotten


@patch("app.api.idempotency.get_redis", return_value=None)
class TestRememberFirstInMemory:
    """REDIS_URL 이 없을 때(로컬 · 테스트) — 같은 약속을 프로세스 메모리로 지킨다."""

    def setup_method(self):
        idempotency.clear()

    def teardown_method(self):
        idempotency.clear()

    async def test_처음이면_None_두_번째는_처음_run(self, _mock):
        assert await _first("run-1") is None
        assert await _first("run-2") == "run-1"
        assert await idempotency.recall(**_SCOPE, key="nx") == "run-1"

    async def test_동시에_여덟_번_와도_하나만_처음이다(self, _mock):
        runs = [f"run-{i}" for i in range(8)]

        results = await asyncio.gather(*(_first(run) for run in runs))

        assert results.count(None) == 1
        assert set(results) - {None} == {runs[results.index(None)]}

    async def test_못_지운_실패_run_이_적혀_있으면_덮어쓴다(self, _mock):
        await _first("run-x")
        idempotency._forgotten.add("run-x")

        assert await _first("run-y") is None
        assert await idempotency.recall(**_SCOPE, key="nx") == "run-y"

    async def test_run_id_가_아닌_값이면_503(self, _mock):
        idempotency._seen[(PID, "POST", "/i", "nx")] = {"id": "evt-1"}

        with pytest.raises(ApiError) as caught:
            await _first("run-1")

        assert caught.value.status == 503


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
    assert len(windows) == 13  # 아이 삭제 DELETE /children/{cid} 가 더해졌다 (#751)
    assert [w for w, ok in windows.items() if not ok] == []
