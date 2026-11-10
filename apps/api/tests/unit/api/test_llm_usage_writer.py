"""사용량 writer (#710) — 큐를 비워 저장하고, 실패해도 죽지 않는다.

DB 는 부르지 않는다. `insert_usage` 와 세션은 가짜이고, 큐 · 귀속은 진짜 `core/llm_usage` 다.
"""

import asyncio
import contextlib
import logging
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api import llm_usage_writer
from app.core import llm_usage

LOGGER = "app.api.llm_usage_writer"
SECRET = "synthetic-secret-detail"
"""예외 메시지에 싣는 값. 로그에 이 글자가 나오면 메시지가 샌 것이다."""


@pytest.fixture(autouse=True)
def _clean():
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    llm_usage_writer._failing = False
    yield
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    llm_usage_writer._failing = False


class _Session:
    def __init__(self, ledger: "_Ledger") -> None:
        self._ledger = ledger

    async def __aenter__(self) -> "_Session":
        self._ledger.opened += 1
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def commit(self) -> None:
        self._ledger.commits += 1


class _Ledger:
    """가짜 세션 · 가짜 insert_usage 가 본 것. `outcomes` 는 호출마다 하나씩 소비한다."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.opened = 0
        self.commits = 0
        self.batches: list[list[llm_usage.UsageRecord]] = []

    def open_session(self) -> _Session:
        return _Session(self)

    async def insert(self, session, batch) -> int:
        self.batches.append(list(batch))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "block":
            await asyncio.Event().wait()  # DB 가 응답하지 않는다 — 취소돼야 끝난다
        return len(batch)


def _install(monkeypatch, ledger: _Ledger) -> None:
    monkeypatch.setattr(llm_usage_writer, "insert_usage", ledger.insert)


def _record_many(count: int, *, enable: bool = True) -> None:
    """귀속 안에서 count 번 기록한다. writer 가 켜는지 보는 테스트는 enable=False 로 부른다."""
    if enable:
        llm_usage.enable()
    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        for _ in range(count):
            llm_usage.record(role="food", provider="gw.example", model="m", prompt_tokens=10)


def _integrity_error() -> IntegrityError:
    return IntegrityError("insert", {}, Exception(SECRET))


async def test_flush_는_큐를_비우고_쓴_건수를_돌려준다(monkeypatch):
    ledger = _Ledger()
    _install(monkeypatch, ledger)
    _record_many(3)

    assert await llm_usage_writer.flush(ledger.open_session) == 3

    assert llm_usage.drain() == []
    assert ledger.commits == 1
    assert [len(b) for b in ledger.batches] == [3]


async def test_IntegrityError_면_새_세션으로_한_번_다시_쓴다(monkeypatch, caplog):
    # 탈퇴가 보호자 행을 지우는 커밋과 겹친 경우 — 다시 하면 LEFT JOIN 이 보호자 칸을 비운다
    ledger = _Ledger(_integrity_error())
    _install(monkeypatch, ledger)
    _record_many(3)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert await llm_usage_writer.flush(ledger.open_session) == 3

    assert ledger.opened == 2
    assert ledger.commits == 1  # 첫 시도는 commit 에 닿지 못했다
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_다시_해도_IntegrityError_면_묶음을_버리고_죽지_않는다(monkeypatch):
    ledger = _Ledger(_integrity_error(), _integrity_error())
    _install(monkeypatch, ledger)
    _record_many(3)

    assert await llm_usage_writer.flush(ledger.open_session) == 0

    assert ledger.opened == 2  # 한 번만 다시 한다
    assert ledger.commits == 0
    assert llm_usage.drain() == []  # 다시 넣지 않는다 — 스키마 버그면 영원히 돈다


async def test_다른_예외는_다시_하지_않고_묶음을_버린다(monkeypatch):
    ledger = _Ledger(RuntimeError(SECRET))
    _install(monkeypatch, ledger)
    _record_many(3)

    assert await llm_usage_writer.flush(ledger.open_session) == 0

    assert ledger.opened == 1
    assert llm_usage.drain() == []


async def test_실패하면_그_묶음만_버리고_나머지는_다음_주기로_남긴다(monkeypatch):
    ledger = _Ledger(RuntimeError(SECRET))
    _install(monkeypatch, ledger)
    _record_many(501)  # drain() 기본 묶음은 500건이다

    assert await llm_usage_writer.flush(ledger.open_session) == 0

    assert len(llm_usage.drain(limit=10_000)) == 1


async def test_연속_실패는_첫_번만_ERROR(monkeypatch, caplog):
    ledger = _Ledger(RuntimeError(SECRET), RuntimeError(SECRET))  # 세 번째부터는 성공한다
    _install(monkeypatch, ledger)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        _record_many(2)
        await llm_usage_writer.flush(ledger.open_session)
        _record_many(2)
        await llm_usage_writer.flush(ledger.open_session)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert (len(errors), len(warnings)) == (1, 1)

        # 고쳐지면 한 줄로 알린다
        _record_many(2)
        await llm_usage_writer.flush(ledger.open_session)
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1

    # 🚨 예외 메시지(여기서는 DB 오류의 DETAIL 자리)는 로그에 없다
    assert SECRET not in caplog.text


async def test_run_writer_는_멈출_때_남은_것을_쓴다(monkeypatch):
    ledger = _Ledger()
    _install(monkeypatch, ledger)
    task = asyncio.create_task(llm_usage_writer.run_writer(ledger.open_session, interval=60))
    await asyncio.sleep(0)  # writer 가 enable() 할 틈

    _record_many(1, enable=False)  # 켜는 것은 writer 의 몫이다
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert [len(b) for b in ledger.batches] == [1]
    # 멈춘 뒤에는 모으지 않는다
    _record_many(1, enable=False)
    assert llm_usage.drain() == []


async def test_run_writer_는_주기마다_큐를_비운다(monkeypatch):
    ledger = _Ledger()
    _install(monkeypatch, ledger)
    task = asyncio.create_task(llm_usage_writer.run_writer(ledger.open_session, interval=0.01))
    await asyncio.sleep(0)

    _record_many(2, enable=False)
    for _ in range(100):
        if ledger.batches:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert [len(b) for b in ledger.batches] == [2]


async def test_DB_연결이_아프면_묶음을_큐에_되돌렸다가_다음_주기에_쓴다(monkeypatch, caplog):
    """연결 · 풀 오류는 잠깐일 수 있다 (#710 리뷰).

    버리면 그 시간대의 사용량이 제공처 콘솔 합계에서 빠진다.
    """
    ledger = _Ledger(OperationalError("insert", {}, Exception(SECRET)))
    _install(monkeypatch, ledger)
    _record_many(3)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert await llm_usage_writer.flush(ledger.open_session) == 0
        assert ledger.commits == 0
        # 되돌려 놨으니 다음 주기에 같은 3건이 나간다
        assert await llm_usage_writer.flush(ledger.open_session) == 3

    assert [len(b) for b in ledger.batches] == [3, 3]
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert SECRET not in caplog.text


async def test_DB_가_계속_아파도_큐_상한이_메모리를_지킨다(monkeypatch):
    ledger = _Ledger(*[OperationalError("insert", {}, Exception(SECRET)) for _ in range(3)])
    _install(monkeypatch, ledger)
    _record_many(llm_usage.MAX_PENDING)

    for _ in range(3):
        assert await llm_usage_writer.flush(ledger.open_session) == 0

    # 500건씩 꺼냈다가 되돌렸을 뿐이라 한 건도 줄지 않았다
    assert len(llm_usage.drain(limit=20_000)) == llm_usage.MAX_PENDING


async def test_쓰는_도중_취소되면_묶음을_되돌리고_마지막_flush_가_쓴다(monkeypatch):
    ledger = _Ledger("block")  # 첫 INSERT 는 응답이 없고, 마지막 flush 의 것은 성공한다
    _install(monkeypatch, ledger)
    task = asyncio.create_task(llm_usage_writer.run_writer(ledger.open_session, interval=0.01))
    await asyncio.sleep(0)
    _record_many(2, enable=False)
    for _ in range(200):
        if ledger.batches:
            break
        await asyncio.sleep(0.01)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert [len(b) for b in ledger.batches] == [2, 2]  # 취소된 시도 + 마지막 flush
    assert ledger.commits == 1


async def test_마지막_flush_가_멈춰도_시간_안에_끝낸다(monkeypatch, caplog):
    """DB 가 안 닿는 채로 종료하면 lifespan 이 여기서 SIGKILL 까지 붙들리면 안 된다 (#710 리뷰)."""
    monkeypatch.setattr(llm_usage_writer, "FINAL_FLUSH_TIMEOUT_S", 0.05)
    ledger = _Ledger("block")
    _install(monkeypatch, ledger)
    task = asyncio.create_task(llm_usage_writer.run_writer(ledger.open_session, interval=60))
    await asyncio.sleep(0)
    _record_many(1, enable=False)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    assert task.done()
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


async def test_flush_밖의_예외가_나도_writer_는_계속_돈다(monkeypatch, caplog):
    calls: list[int] = []

    async def flaky_flush(open_session=None) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(SECRET)
        return 0

    monkeypatch.setattr(llm_usage_writer, "flush", flaky_flush)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        task = asyncio.create_task(llm_usage_writer.run_writer(None, interval=0.01))
        for _ in range(200):
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.01)
        assert not task.done()  # 한 번 터졌어도 죽지 않았다
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(calls) >= 3
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert SECRET not in caplog.text
