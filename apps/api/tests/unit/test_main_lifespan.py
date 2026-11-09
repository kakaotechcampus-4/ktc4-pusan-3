"""서버 시작 · 종료 때 프로세스에 하나씩 두는 클라이언트 (#380 · #713).

날씨 · 행사 · 병원 어댑터는 공공데이터포털 키가 있을 때만 만들고, 종료할 때 닫는다. 키가 없으면
None 이라 Activity 는 날씨를 묻지 않고 행사도 찾지 않으며, Health 병원 찾기는 `place.unavailable` 로
닫힌다.

사용량 writer(#710)는 키와 상관없이 늘 뜬다 — 이 동안만 `core/llm_usage` 가 기록을 모은다.
"""

import asyncio
from uuid import uuid4

from fastapi import FastAPI

from app import main
from app.api.runs import registry
from app.core import llm_usage
from app.domains.memory.curator import trigger as curator_trigger
from app.integrations.hospital.client import NmcHospitals
from app.integrations.tour.events import TourEvents
from app.integrations.weather.source import PublicWeather


async def test_키가_없으면_날씨_어댑터를_만들지_않는다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "")
    app = FastAPI()

    async with main.lifespan(app):
        assert app.state.activity_weather is None
        assert app.state.activity_events is None
        assert app.state.health_places is None


async def test_키가_있으면_만들고_종료할_때_닫는다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "test-key")
    closed: list[bool] = []

    async def fake_aclose(self) -> None:
        closed.append(True)

    monkeypatch.setattr(PublicWeather, "aclose", fake_aclose)
    app = FastAPI()

    async with main.lifespan(app):
        assert isinstance(app.state.activity_weather, PublicWeather)
        assert closed == []
    assert closed == [True]


async def test_행사_어댑터도_만들고_종료할_때_닫는다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "test-key")
    closed: list[str] = []

    async def fake_aclose(self) -> None:
        closed.append(type(self).__name__)

    monkeypatch.setattr(PublicWeather, "aclose", fake_aclose)
    monkeypatch.setattr(TourEvents, "aclose", fake_aclose)
    app = FastAPI()

    async with main.lifespan(app):
        assert isinstance(app.state.activity_events, TourEvents)
        assert closed == []
    assert sorted(closed) == ["PublicWeather", "TourEvents"]


async def test_키가_있으면_병원_어댑터도_만들고_종료할_때_닫는다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "test-key")
    closed: list[bool] = []

    async def fake_aclose(self) -> None:
        closed.append(True)

    monkeypatch.setattr(NmcHospitals, "aclose", fake_aclose)
    app = FastAPI()

    async with main.lifespan(app):
        assert isinstance(app.state.health_places, NmcHospitals)
        assert closed == []
    assert closed == [True]


async def test_lifespan_동안만_사용량을_모은다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "")
    llm_usage.disable()
    llm_usage.drain(limit=10_000)
    app = FastAPI()

    def record_one() -> None:
        with llm_usage.attribute(parent_id=uuid4(), run_id="run-1", source="input"):
            llm_usage.record(role="food", provider="gw.example", model="m", prompt_tokens=1)

    async with main.lifespan(app):
        await asyncio.sleep(0)  # writer 태스크가 enable() 할 틈
        record_one()
        # 나가기 전에 비운다 — 종료 때의 마지막 flush 가 DB 를 부르지 않게
        assert len(llm_usage.drain()) == 1

    record_one()
    assert llm_usage.drain() == []


async def test_사용량_writer_가_죽어도_클라이언트는_닫는다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "test-key")
    closed: list[str] = []

    async def fake_aclose(self) -> None:
        closed.append(type(self).__name__)

    monkeypatch.setattr(PublicWeather, "aclose", fake_aclose)
    monkeypatch.setattr(TourEvents, "aclose", fake_aclose)
    monkeypatch.setattr(NmcHospitals, "aclose", fake_aclose)

    async def dying_writer(*args, **kwargs) -> None:
        raise RuntimeError("synthetic")

    monkeypatch.setattr(main.llm_usage_writer, "run_writer", dying_writer)
    app = FastAPI()

    async with main.lifespan(app):
        await asyncio.sleep(0)  # writer 가 여기서 죽는다

    # 종료 때 죽은 writer 의 예외가 나가면서 클라이언트 닫기를 건너뛰면 안 된다 (#710 리뷰)
    assert sorted(closed) == ["NmcHospitals", "PublicWeather", "TourEvents"]


# ── drain (#882) — 종료 때 진행 중인 run · Curator 를 끝까지 처리하고 내려간다 ──


def _start_run(coro) -> asyncio.Task[None]:
    channel = registry.open_run(parent_id=uuid4())
    channel.task = asyncio.create_task(coro)
    return channel.task


async def test_종료할_때_진행_중인_run_과_curator_를_끝까지_기다린다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "")
    order: list[str] = []

    async def stopping_writer() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            order.append("writer 멈춤")
            raise

    async def job(name: str) -> None:
        await asyncio.sleep(0.05)
        order.append(name)

    monkeypatch.setattr(main.llm_usage_writer, "run_writer", stopping_writer)
    app = FastAPI()
    try:
        async with main.lifespan(app):
            _start_run(job("run 끝"))
            curator = asyncio.create_task(job("curator 끝"))
            curator_trigger._background_tasks.add(curator)
            curator.add_done_callback(curator_trigger._background_tasks.discard)
    finally:
        registry.clear()

    # 사용량 writer 는 run · Curator 가 다 끝난 뒤에 멈춘다
    # — 그래야 그들의 사용량도 마지막 flush 에 실린다
    assert order == ["run 끝", "curator 끝", "writer 멈춤"]


async def test_drain_시간을_넘긴_run_은_취소해서_정리를_거치게_한다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "")
    monkeypatch.setattr(main, "DRAIN_SECONDS", 0.05)
    cleaned: list[str] = []

    async def slow_run() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            cleaned.append("finally")  # 러너는 여기서 failed 를 내고 키를 놓는다

    app = FastAPI()
    try:
        async with main.lifespan(app):
            task = _start_run(slow_run())
    finally:
        registry.clear()

    assert task.cancelled()
    assert cleaned == ["finally"]
