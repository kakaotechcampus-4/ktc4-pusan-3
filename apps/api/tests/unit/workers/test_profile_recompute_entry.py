"""기억 재계산 배치의 진입점(`_main`) — DB 없이 (#636)

재계산 자체는 통합 테스트가 본다. 여기서는 cron 이 기대는 것만 본다.
  · 실패한 아이가 있으면 종료 코드 1 — cron 로그 · 모니터가 실패를 알아볼 수 있게
  · 기준일은 한국 날짜 — 서버 시계(UTC) 날짜로 세면 cron 이 도는 05시(KST)에 하루가 밀릴 수 있다
  · 로거 이름이 app.* — `python -m` 으로 돌아도 실패 ERROR 가 Discord 알림에 닿는다
"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest

from app.workers import profile_recompute as batch


@pytest.fixture
def fake_db(monkeypatch):
    """세션 열기 · 엔진 정리를 가짜로 바꾸고, run_recompute 가 받은 기준일을 모은다."""
    seen: dict = {}

    @asynccontextmanager
    async def fake_factory():
        yield object()

    class FakeEngine:
        async def dispose(self):
            seen["disposed"] = True

    monkeypatch.setattr(batch, "async_session_factory", fake_factory)
    monkeypatch.setattr(batch, "engine", FakeEngine())
    return seen


def _result(failed: int) -> batch.RecomputeResult:
    return batch.RecomputeResult(children=2, profiles=5, changed=1, failed_children=failed)


@pytest.mark.parametrize(("failed", "code"), [(0, 0), (1, 1)])
async def test_실패한_아이가_있으면_종료_코드_1(fake_db, monkeypatch, failed, code):
    async def fake_run(session, *, today):
        return _result(failed)

    monkeypatch.setattr(batch, "run_recompute", fake_run)

    assert await batch._main() == code
    assert fake_db["disposed"]


async def test_기준일은_한국_날짜다(fake_db, monkeypatch):
    """cron 이 도는 시각(UTC 20:30 = KST 다음날 05:30)에 고정한다 — UTC 날짜로 세면 하루 밀린다.

    실제 시계를 쓰면 낮에는 UTC · KST 날짜가 같아 이 실수를 못 잡는다.
    """
    cron_at = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cron_at.astimezone(tz) if tz else cron_at.replace(tzinfo=None)

    async def fake_run(session, *, today):
        fake_db["today"] = today
        return _result(0)

    monkeypatch.setattr(batch, "datetime", FrozenDatetime)
    monkeypatch.setattr(batch, "run_recompute", fake_run)

    await batch._main()

    assert fake_db["today"] == date(2026, 11, 3)


async def test_재계산이_터져도_엔진은_정리한다(fake_db, monkeypatch):
    async def boom(session, *, today):
        raise RuntimeError("boom")

    monkeypatch.setattr(batch, "run_recompute", boom)

    with pytest.raises(RuntimeError):
        await batch._main()
    assert fake_db["disposed"]


def test_로거가_app_아래에_있어_알림이_닿는다():
    assert batch.log.name == "app.workers.profile_recompute"
