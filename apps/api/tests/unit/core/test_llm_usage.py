"""LLM 사용량 기록 계약 단위 테스트 (BM 산정 — 보호자별 토큰).

부르는 쪽(LLMClient · Curator · OCR)이 믿는 약속만 본다.
    - writer 가 꺼져 있으면 아무것도 하지 않는다 (테스트 · eval · 스크립트)
    - 켜져 있는데 귀속이 없으면 저장하지 않고 WARNING — "항상 보호자별" 의 감시 장치
    - 귀속은 gather · create_task · to_thread 로 퍼진다. run_in_executor 로는 안 퍼진다
    - 기록 때문에 호출이 실패하지 않는다
"""

import asyncio
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest

from app.core import llm_usage


@pytest.fixture(autouse=True)
def _clean_queue() -> Iterator[None]:
    """다른 테스트의 큐 · 켜짐 상태가 새지 않게 앞뒤로 비운다."""
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    yield
    llm_usage.disable()
    llm_usage.drain(limit=10_000)


def _record(**overrides: object) -> None:
    values: dict[str, object] = {
        "role": "food",
        "provider": "example.invalid",
        "model": "model-a",
        "prompt_tokens": 100,
    }
    llm_usage.record(**{**values, **overrides})  # type: ignore[arg-type]


def test_writer_가_꺼져_있으면_큐에도_경고에도_남기지_않는다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        _record()  # 귀속도 없고 꺼져 있다 — eval · 스크립트 모양

    assert llm_usage.drain() == []
    assert caplog.records == []


def test_귀속_안에서_부른_기록은_보호자_run_창구가_붙는다() -> None:
    llm_usage.enable()
    parent_id = uuid4()

    with llm_usage.attribute(parent_id=parent_id, run_id="run-1", source="input"):
        _record(
            role="growth",
            model="model-g",
            prompt_tokens=120,
            cached_prompt_tokens=20,
            completion_tokens=30,
            reasoning_tokens=10,
            latency_ms=450,
        )

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.source) == (parent_id, "run-1", "input")
    assert (row.role, row.provider, row.model) == ("growth", "example.invalid", "model-g")
    assert (row.prompt_tokens, row.cached_prompt_tokens) == (120, 20)
    assert (row.completion_tokens, row.reasoning_tokens, row.latency_ms) == (30, 10, 450)
    assert row.occurred_at.tzinfo is not None


def test_귀속_없이_부르면_저장하지_않고_숫자만_경고한다(caplog: pytest.LogCaptureFixture) -> None:
    llm_usage.enable()

    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        _record(role="memory", model="model-m", prompt_tokens=77, completion_tokens=5)

    assert llm_usage.drain() == []
    [warning] = caplog.records
    assert warning.levelno == logging.WARNING
    message = warning.getMessage()
    # 귀속이 빠진 지점을 찾을 수 있는 값만 — 원문은 애초에 받지 않는다
    assert "role=memory" in message
    assert "model=model-m" in message
    assert "prompt_tokens=77" in message


def test_귀속은_with_를_빠져나오면_풀린다() -> None:
    llm_usage.enable()

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        assert llm_usage.current() is not None

    assert llm_usage.current() is None


async def test_gather_로_돈_자식_태스크도_같은_귀속을_본다() -> None:
    llm_usage.enable()
    parent_id = uuid4()

    async def child(index: int) -> None:
        await asyncio.sleep(0)
        _record(role=f"agent-{index}")

    with llm_usage.attribute(parent_id=parent_id, run_id="run-1", source="input"):
        await asyncio.gather(child(1), child(2))

    rows = llm_usage.drain()
    assert sorted(r.role for r in rows) == ["agent-1", "agent-2"]
    assert {(r.parent_id, r.run_id) for r in rows} == {(parent_id, "run-1")}


async def test_with_가_끝난_뒤에_끝나는_백그라운드_태스크도_귀속을_물려받는다() -> None:
    """Curator 가 이 모양이다 — run 이 끝난 뒤에도 돌고, 기록은 그 run 의 것이다."""
    llm_usage.enable()
    parent_id = uuid4()
    gate = asyncio.Event()

    async def curator() -> None:
        await gate.wait()
        _record(role="curator_judge")

    with llm_usage.attribute(parent_id=parent_id, run_id="run-9", source="input"):
        task = asyncio.create_task(curator())

    assert llm_usage.current() is None  # run 은 이미 끝났다
    gate.set()
    await task

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.role) == (parent_id, "run-9", "curator_judge")


async def test_to_thread_로_돈_스레드도_귀속을_본다() -> None:
    """식단 OCR 이 이 모양이다 — 동기 클라이언트라 스레드에서 돈다."""
    llm_usage.enable()
    parent_id = uuid4()

    with llm_usage.attribute(parent_id=parent_id, run_id="up-1", source="photo"):
        await asyncio.to_thread(_record, role="meal_ocr")

    [row] = llm_usage.drain()
    assert (row.parent_id, row.run_id, row.source, row.role) == (
        parent_id,
        "up-1",
        "photo",
        "meal_ocr",
    )


async def test_run_in_executor_는_귀속을_넘기지_못한다(caplog: pytest.LogCaptureFixture) -> None:
    """OCR 담당에게 to_thread 를 쓰라고 안내하는 근거 — 이 길로는 저장되지 않고 경고만 남는다."""
    llm_usage.enable()
    loop = asyncio.get_running_loop()

    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        with llm_usage.attribute(parent_id=uuid4(), run_id="up-1", source="photo"):
            await loop.run_in_executor(None, _record)

    assert llm_usage.drain() == []
    assert len(caplog.records) == 1


def test_큐가_가득_차면_버리고_경고한다(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_usage, "MAX_PENDING", 2)
    llm_usage.enable()

    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
            for _ in range(3):
                _record()

    assert len(llm_usage.drain()) == 2
    assert len(caplog.records) == 1


def test_drain_은_limit_만큼만_꺼내고_나머지는_남긴다() -> None:
    llm_usage.enable()

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        for _ in range(5):
            _record()

    assert len(llm_usage.drain(limit=3)) == 3
    assert len(llm_usage.drain(limit=3)) == 2


def test_기록_안에서_예외가_나도_부른_쪽으로_내보내지_않는다(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_usage.enable()

    class Broken:
        def put(self, _item: object) -> None:
            raise RuntimeError("secret raw text")

        def qsize(self) -> int:
            return 0

    monkeypatch.setattr(llm_usage, "_queue", Broken())

    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
            _record()  # 올라오면 테스트가 터진다

    [warning] = caplog.records
    assert "RuntimeError" in warning.getMessage()
    assert "secret raw text" not in warning.getMessage()  # 예외 메시지는 싣지 않는다


def test_provider_는_주소에서_host_만_뽑는다() -> None:
    assert llm_usage.provider_of("https://api.example.invalid/v1/") == "api.example.invalid"
    # 키 · 경로 · 쿼리 · 계정 정보는 싣지 않는다
    assert (
        llm_usage.provider_of("https://user:pw@gw.example.invalid:8443/v1?key=secret")
        == "gw.example.invalid"
    )
    assert llm_usage.provider_of("") == "unknown"
    assert llm_usage.provider_of(None) == "unknown"


def test_숫자가_아닌_토큰_값은_0으로_담고_예외를_내지_않는다() -> None:
    """제공처가 None · 문자열 · 음수를 줘도 큐에는 정수만 간다.

    나중 INSERT 가 한 행 때문에 묶음째 실패하지 않게 하려는 것이다.
    """
    llm_usage.enable()

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        llm_usage.record(
            role="food",
            provider="example.invalid",
            model="model-a",
            prompt_tokens=None,
            cached_prompt_tokens="n/a",  # type: ignore[arg-type]
            completion_tokens=-5,
            reasoning_tokens=True,  # type: ignore[arg-type]
            latency_ms=None,
        )

    [row] = llm_usage.drain()
    assert (row.prompt_tokens, row.cached_prompt_tokens, row.completion_tokens) == (0, 0, 0)
    assert (row.reasoning_tokens, row.latency_ms) == (0, 0)


def test_int4_를_넘는_숫자는_0_이다() -> None:
    """한 행의 숫자 하나가 묶음 500행의 INSERT 를 통째로 실패시키지 않게 (#710 리뷰)."""
    llm_usage.enable()

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        _record(prompt_tokens=3_000_000_000, completion_tokens=2**31 - 1)

    [row] = llm_usage.drain()
    assert (row.prompt_tokens, row.completion_tokens) == (0, 2**31 - 1)


def test_글자_칸의_NUL_은_지운다() -> None:
    """NUL 이 든 글자는 Postgres 가 거부해 묶음째 실패한다 (#710 리뷰)."""
    llm_usage.enable()

    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        _record(role="fo\x00od", provider="exa\x00mple.invalid", model="mod\x00el-a")

    [row] = llm_usage.drain()
    assert (row.role, row.provider, row.model) == ("food", "example.invalid", "model-a")


def test_Source_밖의_창구_값이면_저장하지_않고_WARNING(caplog: pytest.LogCaptureFixture) -> None:
    """CHECK 에 걸릴 행은 큐에 들이지 않는다 — 안 그러면 같은 묶음의 다른 행까지 날아간다."""
    llm_usage.enable()

    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        with llm_usage.attribute(
            parent_id=uuid4(),
            run_id="run-1",
            source="meal_ocr",  # type: ignore[arg-type]
        ):
            _record()
        with llm_usage.attribute(parent_id=uuid4(), run_id="run-2", source="input"):
            _record()

    assert [row.run_id for row in llm_usage.drain()] == ["run-2"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "meal_ocr" in warnings[0].getMessage()  # 코드가 정한 글자라 남겨도 된다


def test_requeue_는_못_쓴_묶음을_큐_뒤에_되돌리고_상한을_넘는_만큼은_버린다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm_usage.enable()
    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        for _ in range(3):
            _record()
    batch = llm_usage.drain()
    assert len(batch) == 3

    assert llm_usage.requeue(batch) == 3
    assert len(llm_usage.drain()) == 3

    # 상한 — 큐가 MAX_PENDING 에 닿으면 되돌릴 자리가 없다
    with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
        for _ in range(llm_usage.MAX_PENDING - 1):
            _record()
    with caplog.at_level(logging.WARNING, logger="app.core.llm_usage"):
        assert llm_usage.requeue(batch) == 1  # 자리는 하나뿐이다
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
