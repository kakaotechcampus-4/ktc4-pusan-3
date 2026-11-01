"""기상청 격자 · 위경도 표 → 시군구 대표 지점 표를 `app/agents/activity/areas_data.py` 로 굽는다.

    cd apps/api
    uv run --with openpyxl python -m scripts.bake_activity_areas <격자.xlsx>

엑셀은 공공데이터포털 참고문서에 들어 있다 (저장소에 넣지 않는다).
  - 단기예보 조회서비스(15084084) 활용가이드 zip 의 `…_격자_위경도(YYMM).xlsx`
날씨 시군구 표(`scripts/bake_weather_areas.py`)와 같은 엑셀이다. 행정구역이 바뀌면 둘 다
다시 굽는다.

만드는 것
  - DISTRICTS: (시도, 시군구, 위도, 경도) — 시군구 행(3단계가 빈 행)의 대표 지점. 대개 시군구청
    자리다. 보호자가 "해운대 가서" 처럼 지역을 말하면 이 지점에서 장소와 날씨를 본다
    (`app/agents/activity/areas.py`). 위경도는 소수 넷째 자리(약 10m)까지만 싣는다 — 쓸 때 다시
    둘째 자리로 흐린다

🚨 모르는 시도 이름이 나오면 멈춘다 — 행정구역이 바뀐 것이다. `areas.PROVINCE_ALIASES` 를 고치고
   다시 돌린다.
"""

import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from app.agents.activity.areas import PROVINCE_ALIASES
from scripts.bake_holidays import BakeError

OUTPUT = Path(__file__).resolve().parents[1] / "app" / "agents" / "activity" / "areas_data.py"

# 격자 표에 있지만 육지 생활권이 아니라 굽지 않는 시도 (`bake_weather_areas.SKIP_PROVINCES`)
SKIP_PROVINCES = frozenset({"이어도"})
# 격자 표의 경도 · 위도(초/100) 칸
LON_COLUMN = 13
LAT_COLUMN = 14
# 한국 상자 — 위경도가 뒤바뀐 행을 잡는다 (`domains/place/models.py` KOREA_BOX_CHECK 와 같다)
KOREA_LAT = (33.0, 39.0)
KOREA_LON = (124.0, 132.0)

District = tuple[str, str, float, float]


def parse_districts(rows: Iterable[Sequence[Any]]) -> list[District]:
    """격자 표(`구분 · 행정구역코드 · 1단계 · 2단계 · 3단계 · …`)에서 시군구 행만."""
    districts: list[District] = []
    for row in rows:
        code = str(row[1] or "").strip()
        province = str(row[2] or "").strip()
        if not re.fullmatch(r"\d{10}", code) or province in SKIP_PROVINCES:
            continue
        district = str(row[3] or "").strip()
        if not district or str(row[4] or "").strip():
            continue
        if province not in PROVINCE_ALIASES:
            raise BakeError(f"모르는 시도: {province} — areas.PROVINCE_ALIASES 에 더한다")
        lat, lon = round(float(row[LAT_COLUMN]), 4), round(float(row[LON_COLUMN]), 4)
        if not (KOREA_LAT[0] <= lat <= KOREA_LAT[1] and KOREA_LON[0] <= lon <= KOREA_LON[1]):
            raise BakeError(f"한국 밖 좌표: {province} {district}")
        districts.append((province, district, lat, lon))
    if not districts:
        raise BakeError("시군구가 0곳이다 — 격자 표 모양을 확인한다")
    return districts


_HEADER = '''"""시군구 대표 지점. `scripts/bake_activity_areas.py` 가 만든다 — 직접 고치지 않는다.

출처: 기상청 단기예보 활용가이드의 {source} 표, 시군구 행(대개 시군구청 자리).
보호자 위치가 아니다 — 보호자가 말한 지역의 장소 · 날씨 기준점이다 (`areas.py`).
"""

'''


def render(districts: Sequence[District], *, source: str) -> str:
    lines = [
        _HEADER.format(source=source) + "DISTRICTS: tuple[tuple[str, str, float, float], ...] = ("
    ]
    lines += [f'    ("{p}", "{d}", {lat}, {lon}),' for p, d, lat, lon in districts]
    lines.append(")")
    return "\n".join(lines) + "\n"


def _sheet_rows(path: Path) -> list[tuple[Any, ...]]:
    import openpyxl  # 굽는 때만 쓴다 — 서비스 의존성에 넣지 않는다

    workbook = openpyxl.load_workbook(path, read_only=True)
    return list(workbook.worksheets[0].iter_rows(values_only=True))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    # 파일 이름의 "격자_위경도(2610)" 를 출처 판으로 싣는다
    version = re.search(r"격자_위경도\(\d+\)", path.name)
    try:
        districts = parse_districts(_sheet_rows(path))
    except BakeError as error:
        print(f"멈춤 — {error}", file=sys.stderr)
        return 1
    source = version.group(0) if version else "격자 · 위경도"
    OUTPUT.write_text(render(districts, source=source), encoding="utf-8", newline="\n")
    print(f"시군구 {len(districts)}곳 · 썼다: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
