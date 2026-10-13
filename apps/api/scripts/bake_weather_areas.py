"""기상청 격자 → 시군구 코드 · 특보구역 표를 `app/integrations/weather/areas_data.py` 로 굽는다.

    cd apps/api
    uv run --with openpyxl python -m scripts.bake_weather_areas <격자.xlsx> <특보구역.xlsx>

두 엑셀은 공공데이터포털 참고문서에 들어 있다 (저장소에 넣지 않는다).
  - 격자: 단기예보 조회서비스(15084084) 활용가이드 zip 의 `…_격자_위경도(YYMM).xlsx`
  - 특보구역: 기상특보 조회서비스(15000415) 활용가이드 zip 의 `…_특보구역코드안내(YYMMDD).xlsx`
행정구역 · 특보구역이 바뀌는 발표가 나오면 새 엑셀로 다시 돌린다 (2026 년에만 인천 구 개편 ·
전남광주 통합 · 특보구역 세분화가 있었다).

만드는 것
  - DISTRICTS: (시군구 코드, nx, ny) — 읍면동 대표점의 격자마다 그 시군구. 어댑터가 격자에서 가장
    가까운 행을 골라 시군구를 정한다. 자외선은 이 시군구 코드로 묻는다 — 읍면동 코드는 더 잘게
    위치를 드러내서 쓰지 않는다
  - ZONES_BY_DISTRICT: 시군구 코드 → 그 시군구에 걸리는 특보구역 코드들. 특보는 전국 목록을 한 번에
    받아 이 코드들로 거른다 — 특보 쪽으로는 위치가 하나도 나가지 않는다

특보구역 고르기 (안전한 쪽으로)
  1. 시도에 해당하는 특보구역 묶음(`PROVINCE_ZONES`) 안에서 시군구 이름이 구역 이름으로 시작하는
     구역을 찾는다(가장 긴 이름). 그 구역과 하위 구역(파주시 → 파주시동북부 · 서북부 · 남부),
     상위 구역(경기도)을 다 넣는다 — 하위 구역 중 어디든 발효면 발효로 본다
  2. 못 찾으면 그 시도 묶음 전체를 넣는다. 구 단위 구역이 없는 광역시(서울 4권역 · 부산 3구역 ·
     인천 · 대구)가 여기다. 어느 권역인지 표가 없어서, 그 시 어디든 발효면 발효로 본다
  3. 시도와 특보구역 소속이 다른 곳은 `DISTRICT_ROOTS` 로 직접 정한다(옛 광주 구 · 울릉군).
     군위군은 2023-11 부터 특보구역도 대구 아래라 따로 정하지 않는다

🚨 모르는 시도 이름이 나오면 멈춘다 — 행정구역이 바뀐 것이다. 표를 고치고 다시 돌린다.
"""

import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.bake_holidays import BakeError

OUTPUT = Path(__file__).resolve().parents[1] / "app" / "integrations" / "weather" / "areas_data.py"

# 특보구역 표의 "끝나지 않음" 종료일
OPEN_END = 210012310000

# 격자 표의 시도 이름 → 특보구역 표의 시도 묶음
PROVINCE_ZONES: dict[str, tuple[str, ...]] = {
    "서울특별시": ("L1100000",),
    "부산광역시": ("L1150000",),
    "대구광역시": ("L1140000",),
    "인천광역시": ("L1110000", "L1014000"),  # 서해5도
    "대전광역시": ("L1120000",),
    "울산광역시": ("L1160000",),
    "세종특별자치시": ("L1170000",),
    "경기도": ("L1010000",),
    "강원특별자치도": ("L1020000",),
    "충청북도": ("L1040000",),
    "충청남도": ("L1030000",),
    "전북특별자치도": ("L1060000",),
    "전남광주통합특별시": ("L1050000", "L1052400"),  # 흑산도.홍도. 옛 광주는 DISTRICT_ROOTS
    "경상북도": ("L1070000", "L1600000"),  # 울릉도.독도
    "경상남도": ("L1080000",),
    "제주특별자치도": ("L1090000",),
}
# 시도가 아니라 이 묶음에서 찾는 시군구 (코드 앞 5자리)
DISTRICT_ROOTS: dict[str, tuple[str, ...]] = {
    # 옛 광주광역시 구 — 특보구역은 아직 광주(L1130000)다
    **{code: ("L1130000",) for code in ("12210", "12240", "12270", "12300", "12330")},
    "47940": ("L1600000",),  # 울릉군 — 특보구역 이름이 "울릉도.독도" 라 이름으로 안 맞는다
}
# 격자 표에 있지만 육지 생활권이 아니라 굽지 않는 시도
SKIP_PROVINCES = frozenset({"이어도"})


@dataclass(frozen=True)
class Zone:
    code: str
    parent: str
    short: str  # REG_KO — "광명"
    name: str  # REG_NAME — "광명시"


@dataclass(frozen=True)
class Region:
    code: str  # 행정구역코드 10자리
    province: str  # 1단계
    district: str  # 2단계
    town: str  # 3단계
    nx: int
    ny: int

    @property
    def district_code(self) -> str:
        return self.code[:5] + "00000"


def parse_zones(rows: Iterable[Sequence[Any]]) -> dict[str, Zone]:
    """특보구역 표 Sheet2(`REG_ID · TM_ST · TM_ED · REG_SP · REG_UP · REG_KO · REG_NAME`)에서 지금
    쓰는 구역만. 끝난 구역(TM_ED 가 열려 있지 않음)은 버린다."""
    zones: dict[str, Zone] = {}
    for row in rows:
        code = str(row[0] or "").strip()
        if not re.fullmatch(r"L\d{7}", code) or row[2] != OPEN_END:
            continue
        zones[code] = Zone(
            code, str(row[4] or "").strip(), str(row[5]).strip(), str(row[6]).strip()
        )
    if not zones:
        raise BakeError("특보구역이 0개다 — Sheet2 모양을 확인한다")
    return zones


def parse_regions(rows: Iterable[Sequence[Any]]) -> list[Region]:
    """격자 표(`구분 · 행정구역코드 · 1단계 · 2단계 · 3단계 · 격자 X · 격자 Y …`)에서
    시군구 이하 행."""
    regions: list[Region] = []
    for row in rows:
        code = str(row[1] or "").strip()
        if not re.fullmatch(r"\d{10}", code) or not row[3]:
            continue
        regions.append(
            Region(
                code,
                str(row[2]).strip(),
                str(row[3]).strip(),
                str(row[4] or "").strip(),
                int(row[5]),
                int(row[6]),
            )
        )
    if not regions:
        raise BakeError("행정구역이 0개다 — 격자 표 모양을 확인한다")
    return regions


def _base(name: str) -> str:
    return re.sub(r"\(.*?\)", "", name).strip()


def _subtree(roots: Iterable[str], zones: dict[str, Zone]) -> set[str]:
    children: dict[str, list[str]] = {}
    for zone in zones.values():
        children.setdefault(zone.parent, []).append(zone.code)
    found: set[str] = set()
    stack = [root for root in roots if root in zones]
    while stack:
        code = stack.pop()
        if code not in found:
            found.add(code)
            stack.extend(children.get(code, ()))
    return found


def _ancestors(code: str, zones: dict[str, Zone]) -> set[str]:
    found: set[str] = set()
    while code in zones and zones[code].parent in zones:
        code = zones[code].parent
        if code == "L1000000":  # 전국
            break
        found.add(code)
    return found


def zones_for(region: Region, zones: dict[str, Zone]) -> tuple[tuple[str, ...], bool]:
    """시군구 하나에 걸리는 특보구역 코드들(정렬)과, 이름으로 찾았는지(False 면 시도 묶음 전체)."""
    roots = DISTRICT_ROOTS.get(region.code[:5]) or PROVINCE_ZONES.get(region.province)
    if roots is None:
        raise BakeError(f"모르는 시도: {region.province} — PROVINCE_ZONES 에 더한다")
    pool = _subtree(roots, zones)
    # 묶음의 맨 위(부산 · 서울 같은 시도 자체)는 이름 대조에서 뺀다 — "부산진구" 가 "부산" 으로
    # 시작해도 구역을 찾은 게 아니다
    matches = [
        code
        for code in pool - set(roots)
        if any(
            stem and region.district.startswith(stem)
            for stem in (_base(zones[code].name), zones[code].short)
        )
    ]
    if not matches:
        return tuple(sorted(pool)), False
    longest = max(len(_base(zones[code].name)) for code in matches)
    chosen = [code for code in matches if len(_base(zones[code].name)) == longest]
    picked = _subtree(chosen, zones)
    for code in chosen:
        picked |= _ancestors(code, zones)
    return tuple(sorted(picked)), True


@dataclass(frozen=True)
class Baked:
    cells: list[tuple[str, int, int]]  # DISTRICTS
    by_district: dict[str, tuple[str, ...]]  # ZONES_BY_DISTRICT
    whole_province: list[str]  # 이름으로 못 찾아 시도 묶음 전체로 본 시군구 ("부산광역시 해운대구")


def build(regions: list[Region], zones: dict[str, Zone]) -> Baked:
    """같은 시군구 · 같은 격자는 한 행이다."""
    kept = [r for r in regions if r.province not in SKIP_PROVINCES]
    cells = sorted({(r.district_code, r.nx, r.ny) for r in kept})
    by_district: dict[str, tuple[str, ...]] = {}
    whole: list[str] = []
    for region in kept:
        if region.district_code in by_district:
            continue
        codes, matched = zones_for(region, zones)
        by_district[region.district_code] = codes
        if not matched:
            whole.append(f"{region.province} {region.district}")
    return Baked(cells, dict(sorted(by_district.items())), whole)


_HEADER = '''"""기상청 격자 → 시군구 코드 · 특보구역. `scripts/bake_weather_areas.py` 가 만든다 —
직접 고치지 않는다.

출처: 기상청 단기예보 활용가이드의 격자 · 위경도 표, 기상특보 활용가이드의 특보구역 코드 표.
좌표는 싣지 않는다. 시군구 코드는 자외선 조회에, 특보구역 코드는 전국 특보를 거르는 데 쓴다.
"""

'''


def render(cells: list[tuple[str, int, int]], by_district: dict[str, tuple[str, ...]]) -> str:
    lines = [_HEADER + "DISTRICTS: tuple[tuple[str, int, int], ...] = ("]
    lines += [f'    ("{code}", {nx}, {ny}),' for code, nx, ny in cells]
    lines += [")", "", "ZONES_BY_DISTRICT: dict[str, tuple[str, ...]] = {"]
    # ruff format 이 고치지 않는 모양으로 쓴다 — 한 줄에 들어가면 한 줄(뒤 쉼표 없음),
    # 아니면 한 칸씩
    for code, zone_codes in by_district.items():
        inner = ", ".join(f'"{zone}"' for zone in zone_codes)
        one_line = f'    "{code}": ({inner}{"," if len(zone_codes) == 1 else ""}),'
        if len(one_line) <= 100:
            lines.append(one_line)
        else:
            lines.append(f'    "{code}": (')
            lines += [f'        "{zone}",' for zone in zone_codes]
            lines.append("    ),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _sheet_rows(path: Path, index: int) -> list[tuple[Any, ...]]:
    import openpyxl  # 굽는 때만 쓴다 — 서비스 의존성에 넣지 않는다

    workbook = openpyxl.load_workbook(path, read_only=True)
    return list(workbook.worksheets[index].iter_rows(values_only=True))


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    grid_path, zone_path = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        regions = parse_regions(_sheet_rows(grid_path, 0))
        zones = parse_zones(_sheet_rows(zone_path, 1))
        baked = build(regions, zones)
    except BakeError as error:
        print(f"멈춤 — {error}", file=sys.stderr)
        return 1
    print(f"격자 {len(baked.cells)}칸 · 시군구 {len(baked.by_district)}곳")
    print(f"시도 묶음 전체로 보는 시군구 {len(baked.whole_province)}곳 — 구 단위 특보구역이 없다:")
    for name in baked.whole_province:
        print(f"  {name}")
    OUTPUT.write_text(render(baked.cells, baked.by_district), encoding="utf-8", newline="\n")
    print(f"썼다: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
