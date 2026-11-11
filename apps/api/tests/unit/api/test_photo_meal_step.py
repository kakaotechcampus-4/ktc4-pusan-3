"""사진 job 의 진행 문구 — 급식표로 판정되면 같은 1단계의 문구만 바꿔 다시 보낸다.

급식표는 표 읽기가 1~2분 걸려, 문구가 하나뿐이면 멈춘 것처럼 보인다. 칸 수(total)는 그대로다 —
화면 줄이 흔들리지 않게. 알림은 읽기 스레드에서 오므로 이벤트 루프로 넘겨 발행해야 한다.
모델 · DB 는 부르지 않는다.
"""

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.runs import photo_run, registry

READING = SimpleNamespace(notice=SimpleNamespace(raw_text="지어낸 글자"))
FIRST = "사진에서 글자를 찾고 있어요"
MEAL = "급식표를 날짜별로 읽고 있어요"
LAST = "읽은 것을 정리하고 있어요"


class _Channel:
    """발행된 이벤트와 그때의 스레드를 남긴다."""

    run_id = "run_test"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.threads: list[int] = []

    def publish(self, event: tuple[str, dict]) -> None:
        self.events.append(event)
        self.threads.append(threading.get_ident())


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch):
    async def keep(run, *, now=None) -> bool:
        return True

    monkeypatch.setattr(photo_run, "keep", keep)
    monkeypatch.setattr(photo_run, "build_entries", lambda run, reading, *, today: [{"id": "pe_0"}])
    yield
    registry.clear()


def _job():
    run = photo_run.open_photo_run(run_id="run_test", parent_id=uuid.uuid4(), child_id=uuid.uuid4())
    return photo_run.photo_job(
        run, data=b"image", received_at=datetime.now(UTC), declared_lane="document"
    )


def _steps(events: list[tuple[str, dict]]) -> list:
    return [
        (name, payload["index"], payload["total"], payload["label"]) if name == "step" else name
        for name, payload in events
    ]


async def test_급식표면_1단계_문구를_바꿔_다시_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    reader_thread: list[int] = []

    def read_photo(data, *, received_at, meal_deadline, on_meal_plan):
        reader_thread.append(threading.get_ident())
        on_meal_plan()
        return READING

    monkeypatch.setattr(photo_run.entrypoint, "read_photo", read_photo)
    channel = _Channel()

    await _job()(channel)

    assert _steps(channel.events) == [
        ("step", 1, 2, FIRST),
        ("step", 1, 2, MEAL),
        "lane",
        ("step", 2, 2, LAST),
        "parsed",
        "done",
    ]
    # 읽기는 다른 스레드에서 돌았고, 발행은 전부 이벤트 루프 스레드에서 했다
    assert reader_thread[0] != threading.get_ident()
    assert set(channel.threads) == {threading.get_ident()}


async def test_공지면_순서가_그대로다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        photo_run.entrypoint,
        "read_photo",
        lambda data, *, received_at, meal_deadline, on_meal_plan: READING,
    )
    channel = _Channel()

    await _job()(channel)

    assert _steps(channel.events) == [
        ("step", 1, 2, FIRST),
        "lane",
        ("step", 2, 2, LAST),
        "parsed",
        "done",
    ]


async def test_시간이_넘은_뒤_늦게_온_알림은_나가지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 스레드는 밖에서 끊지 못한다 — failed(timeout) 을 낸 뒤에도 읽기가 돌다 급식표라고 알릴 수 있다
    monkeypatch.setattr(photo_run, "PHOTO_READ_DEADLINE_SECONDS", 0.05)
    told = threading.Event()

    def read_photo(data, *, received_at, meal_deadline, on_meal_plan):
        time.sleep(0.3)
        on_meal_plan()
        told.set()
        return READING

    monkeypatch.setattr(photo_run.entrypoint, "read_photo", read_photo)
    channel = registry.open_run(parent_id=uuid.uuid4())

    await _job()(channel)
    assert await asyncio.to_thread(told.wait, 2)
    await asyncio.sleep(0.01)  # 넘겨 둔 발행이 루프에서 돌 틈

    assert _steps(channel.events) == [("step", 1, 2, FIRST), "failed"]
    assert channel.events[-1][1]["reason"] == "timeout"
