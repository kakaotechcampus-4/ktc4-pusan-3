"""네 조회를 묶은 날씨 어댑터 — Activity `WeatherSource` 포트의 실제 구현.

`app/api` 가 프로세스에 하나를 만들어 Activity 에 주입하고, 끝날 때 `aclose()` 한다. 네 조회는
따로 실패한다 — 하나가 죽어도 나머지는 온다 (D8). 묶기만 하고 아무것도 해석하지 않는다.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime

import httpx

from app.core.weather_raw import RawAdvisories, RawAir, RawForecast, WeatherGrid
from app.integrations.weather.advisories import KmaAdvisories
from app.integrations.weather.air import AirKorea
from app.integrations.weather.forecast import KmaForecast
from app.integrations.weather.uv import KmaUv

log = logging.getLogger(__name__)


class PublicWeather:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._forecast = KmaForecast(transport=transport, now=now)
        self._air = AirKorea(transport=transport, now=now)
        self._uv = KmaUv(transport=transport, now=now)
        self._advisories = KmaAdvisories(transport=transport, now=now)

    async def aclose(self) -> None:
        """넷을 다 닫는다. 하나가 실패해도 나머지는 닫는다 — 클라이언트가 남지 않게 (#346 리뷰)."""
        for part in (self._forecast, self._air, self._uv, self._advisories):
            try:
                await part.aclose()
            except Exception as error:  # 닫기 실패는 종류만 남긴다 — 문구에 주소가 들 수 있다
                log.warning(
                    "날씨 어댑터 닫기 실패 part=%s error=%s",
                    type(part).__name__,
                    type(error).__name__,
                )

    async def forecast(self, *, grid: WeatherGrid, day: date) -> RawForecast:
        return await self._forecast.forecast(grid=grid, day=day)

    async def air_quality(self, *, grid: WeatherGrid) -> RawAir:
        return await self._air.air_quality(grid=grid)

    async def uv(self, *, grid: WeatherGrid, day: date) -> str | None:
        return await self._uv.uv(grid=grid, day=day)

    async def advisories(self, *, grid: WeatherGrid) -> RawAdvisories:
        return await self._advisories.advisories(grid=grid)
