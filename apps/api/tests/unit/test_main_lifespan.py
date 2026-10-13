"""서버 시작 · 종료 때 프로세스에 하나씩 두는 클라이언트 (#380).

날씨 어댑터는 공공데이터포털 키가 있을 때만 만들고, 종료할 때 닫는다. 키가 없으면 None 이라
Activity 는 날씨를 묻지 않고 실내 전용으로 돈다.
"""

from fastapi import FastAPI

from app import main
from app.integrations.weather.source import PublicWeather


async def test_키가_없으면_날씨_어댑터를_만들지_않는다(monkeypatch):
    monkeypatch.setattr(main.settings, "DATA_GO_KR_SERVICE_KEY", "")
    app = FastAPI()

    async with main.lifespan(app):
        assert app.state.activity_weather is None


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
