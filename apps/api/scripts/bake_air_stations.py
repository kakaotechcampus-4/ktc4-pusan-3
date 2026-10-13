"""에어코리아 측정소 목록을 받아 `app/integrations/weather/stations_data.py` 로 굽는다.

    cd apps/api && uv run python -m scripts.bake_air_stations

측정소는 1년에 몇 곳 바뀌는 정도라 가끔(분기에 한 번쯤) 돌린다. 서비스는 런타임에 목록 API 를
부르지 않고 구운 파일에서 가장 가까운 측정소를 고른다 (설계 D8 · D9).

- 좌표 대신 기상청 격자(nx, ny)로 굽는다. 날씨 어댑터는 격자만 받기 때문이다 — 보호자 좌표가
  어댑터에 들어오지 않는다 (4-3). 측정소 좌표 → 격자 변환은 `app/rules/kma_grid.py` 다.
- 🚨 `dmX` 가 위도, `dmY` 가 경도다 — 흔한 X=경도 관례와 반대다.
- 사람이 사는 곳의 공기를 재는 `도시대기` · `교외대기` 만 남긴다. `도로변대기` 는 차도 옆이라
  높게, `항만` · `국가배경농도(도서)` 는 사는 곳과 떨어져 있다.
- PM10 · PM2.5 를 둘 다 재는 측정소만 남긴다. 하나라도 없으면 미세먼지 판정이 반쪽이 된다.
- 바뀐 측정소(새로 생기거나 빠진 곳)를 출력한다. 굽고 나면 `git diff` 로 확인하고 커밋한다.

🚨 인증키를 출력하거나 예외 메시지에 넣지 않는다. 저장소가 public 이다 (루트 CLAUDE.md §9).
"""

import sys
from pathlib import Path
from typing import Any

import httpx

from app.integrations.weather.stations_data import STATIONS
from app.rules.kma_grid import latlon_to_grid
from scripts.bake_holidays import BakeError, read_key

ENDPOINT = "https://apis.data.go.kr/B552584/MsrstnInfoInqireSvc/getMsrstnList"
OUTPUT = (
    Path(__file__).resolve().parents[1] / "app" / "integrations" / "weather" / "stations_data.py"
)
TIMEOUT_SECONDS = 20.0
# 전국 측정소는 700곳 남짓이다. 한 번에 받는다
ROWS = 1000

KEEP_NETWORKS = frozenset({"도시대기", "교외대기"})
REQUIRED_ITEMS = ("PM10", "PM2.5")

Station = tuple[str, int, int]  # (측정소 이름, nx, ny)

_HEADER = '''"""에어코리아 대기질 측정소와 기상청 격자. `scripts/bake_air_stations.py` 가 만든다 —
직접 고치지 않는다.

출처: 에어코리아 측정소정보 `MsrstnInfoInqireSvc/getMsrstnList`. `도시대기` · `교외대기` 중
PM10 · PM2.5 를 둘 다 재는 곳만. 좌표는 싣지 않고 격자만 싣는다.
"""

'''


def parse_stations(payload: dict[str, Any]) -> list[Station]:
    """`getMsrstnList` JSON → (이름, nx, ny) 목록. 이름 순."""
    response = payload.get("response") or {}
    header = response.get("header") or {}
    if header.get("resultCode") != "00":
        raise BakeError(f"resultCode={header.get('resultCode')} {header.get('resultMsg')}")
    rows = (response.get("body") or {}).get("items") or []
    stations: list[Station] = []
    for row in rows:
        if row.get("mangName") not in KEEP_NETWORKS:
            continue
        measured = {part.strip() for part in str(row.get("item") or "").split(",")}
        if not all(item in measured for item in REQUIRED_ITEMS):
            continue
        try:
            lat, lon = float(row["dmX"]), float(row["dmY"])  # dmX 가 위도다
            nx, ny = latlon_to_grid(lat, lon)
        except (KeyError, TypeError, ValueError):
            raise BakeError(f"좌표를 읽지 못한 측정소: {row.get('stationName')}") from None
        stations.append((str(row["stationName"]), nx, ny))
    if not stations:
        raise BakeError("남은 측정소가 0곳이다")
    return sorted(stations)


def render(stations: list[Station]) -> str:
    """구운 파일 본문. 같은 입력이면 늘 같은 글이다 (이름 순)."""
    lines = [_HEADER + "STATIONS: tuple[tuple[str, int, int], ...] = ("]
    lines += [f'    ("{name}", {nx}, {ny}),' for name, nx, ny in stations]
    lines.append(")")
    return "\n".join(lines) + "\n"


def changes(before: tuple[Station, ...], after: list[Station]) -> list[str]:
    """새로 생기거나 빠진 측정소. 같은 이름의 격자가 바뀐 것도 센다."""
    old, new = {s[0]: s for s in before}, {s[0]: s for s in after}
    added = [f"+ {name}" for name in sorted(new.keys() - old.keys())]
    removed = [f"- {name}" for name in sorted(old.keys() - new.keys())]
    moved = [f"~ {name}" for name in sorted(old.keys() & new.keys()) if old[name] != new[name]]
    return added + removed + moved


def fetch(key: str) -> list[Station]:
    params = {"serviceKey": key, "pageNo": 1, "numOfRows": ROWS, "returnType": "json"}
    try:
        response = httpx.get(ENDPOINT, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        # 오류 문자열에 요청 주소(인증키 포함)가 들어갈 수 있어 종류만 남긴다
        raise BakeError(f"호출 실패 ({type(error).__name__})") from None
    return parse_stations(payload)


def main() -> int:
    key = read_key()
    if not key:
        print("DATA_GO_KR_SERVICE_KEY 가 없다 — apps/api/.env 에 넣는다.", file=sys.stderr)
        return 1
    try:
        stations = fetch(key)
    except BakeError as error:
        print(f"멈춤 — {error}", file=sys.stderr)
        return 1
    diff = changes(STATIONS, stations)
    print(f"{len(stations)}곳" + ("" if diff else " (바뀐 측정소 없음)"))
    for line in diff:
        print(f"  {line}")
    OUTPUT.write_text(render(stations), encoding="utf-8", newline="\n")
    print(f"썼다: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
