"""위경도를 기상청 단기예보 격자(nx, ny)로 바꾼다.

기상청 동네예보 격자는 람베르트 정각원추도법(LCC) 5km 격자다. 공식과 상수는 기상청
「단기예보 조회서비스 오픈API 활용가이드」의 위경도 → 격자 변환 코드 그대로다.
순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

개인정보 축소 함수이기도 하다. 보호자 위치는 이 격자로만 기상청에 나간다.
"""

import math

_RE = 6371.00877  # 지구 반경 (km)
_GRID = 5.0  # 격자 간격 (km)
_SLAT1 = 30.0  # 표준 위도 1
_SLAT2 = 60.0  # 표준 위도 2
_OLON = 126.0  # 기준점 경도
_OLAT = 38.0  # 기준점 위도
_XO = 43  # 기준점 X 격자
_YO = 136  # 기준점 Y 격자


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위도 · 경도(도)를 기상청 격자 (nx, ny) 로. 값이 범위를 벗어나면 ValueError.

    예외 메시지에 좌표를 넣지 않는다 — 예외는 로그로 흘러간다.
    """
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise ValueError("위경도가 범위를 벗어났다")

    degrad = math.pi / 180.0
    re = _RE / _GRID
    slat1 = _SLAT1 * degrad
    slat2 = _SLAT2 * degrad
    olon = _OLON * degrad
    olat = _OLAT * degrad

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)

    ra = math.tan(math.pi * 0.25 + lat * degrad * 0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon * degrad - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = math.floor(ra * math.sin(theta) + _XO + 0.5)
    ny = math.floor(ro - ra * math.cos(theta) + _YO + 0.5)
    return nx, ny
