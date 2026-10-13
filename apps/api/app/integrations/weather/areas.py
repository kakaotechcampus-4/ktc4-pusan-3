"""격자 → 시군구 · 특보구역. 구운 표(`areas_data.py`)만 읽는다 — 외부를 부르지 않는다."""

from app.core.weather_raw import WeatherGrid
from app.integrations.weather.areas_data import DISTRICTS, ZONES_BY_DISTRICT

# 이 격자 거리(칸, 1칸 = 5km)보다 먼 읍면동 대표점밖에 없으면 시군구를 정하지 않는다 — 바다
# 한가운데 격자에 육지 값을 쓰지 않는다. 대표점은 전국 1,800칸 남짓이라 육지는 거의 1–2칸 안이다
MAX_DISTRICT_CELLS = 6

District = tuple[str, int, int]


def nearest_district(grid: WeatherGrid, districts: tuple[District, ...] = DISTRICTS) -> str | None:
    """격자에서 가장 가까운 읍면동 대표점의 시군구 코드. 같은 거리면 코드 순으로 앞의 것."""
    best: tuple[int, str] | None = None
    for code, nx, ny in districts:
        distance = (nx - grid.nx) ** 2 + (ny - grid.ny) ** 2
        if best is None or (distance, code) < best:
            best = (distance, code)
    if best is None or best[0] > MAX_DISTRICT_CELLS**2:
        return None
    return best[1]


def zones_of(district: str) -> frozenset[str]:
    """그 시군구에 걸리는 특보구역 코드. 표에 없으면 빈 집합 — 호출부가 실패로 다룬다."""
    return frozenset(ZONES_BY_DISTRICT.get(district, ()))
