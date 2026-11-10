"""사진 job 의 읽기 상한 — 모델이 멈춰도 화면은 정해진 시간 뒤 failed(timeout) 을 받는다."""

import time
import uuid
from datetime import UTC, datetime

import pytest

from app.api.runs import photo_run


class _Channel:
    run_id = "run_test"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: tuple[str, dict]) -> None:
        self.events.append(event)


async def test_읽기가_상한을_넘으면_timeout_으로_끝낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(photo_run, "PHOTO_READ_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(
        photo_run.entrypoint, "read_photo", lambda data, *, received_at: time.sleep(0.4)
    )
    run = photo_run.open_photo_run(run_id="run_test", parent_id=uuid.uuid4(), child_id=uuid.uuid4())
    channel = _Channel()
    job = photo_run.photo_job(
        run, data=b"image", received_at=datetime.now(UTC), declared_lane="notice"
    )

    await job(channel)

    name, payload = channel.events[-1]
    assert name == "failed"
    assert payload["reason"] == "timeout"
    assert run.reading is None
    assert not any(event[0] in ("parsed", "done") for event in channel.events)


def test_상한은_실측_최대보다_넉넉하다() -> None:
    # 급식표 한 장이 정상으로 읽히는 데 최대 218초가 걸렸다 (docs/notice/notice-ocr-v1.md).
    # 그보다 짧게 잡으면 읽히는 사진을 끊는다
    assert photo_run.PHOTO_READ_DEADLINE_SECONDS >= 240
