"""위경도 → 기상청 단기예보 격자.

기대값은 기상청 「단기예보 조회서비스」 격자 위경도 표의 시도 대표점이다.
경계 근처 한 칸 차이가 곧 다른 동네 날씨라, 공식을 옮기다 상수 하나가 틀리면 여기서 잡힌다.
"""

import pytest

from app.rules.kma_grid import latlon_to_grid

KNOWN = [
    ("서울특별시", 37.5635694444444, 126.980008333333, (60, 127)),
    ("부산광역시", 35.1770194444444, 129.076952777777, (98, 76)),
    ("대구광역시", 35.8685416666666, 128.603552777777, (89, 90)),
    ("대전광역시", 36.3471194444444, 127.386566666666, (67, 100)),
    ("광주광역시", 35.1569749999999, 126.853363888888, (58, 74)),
    ("제주특별자치도", 33.4856944444444, 126.500333333333, (52, 38)),
]


@pytest.mark.parametrize(("name", "lat", "lon", "expected"), KNOWN, ids=[k[0] for k in KNOWN])
def test_기상청_표와_같은_격자를_낸다(name, lat, lon, expected):
    assert latlon_to_grid(lat, lon) == expected


def test_1km_로_흐린_좌표도_같은_격자다():
    """휴대폰이 둘째 자리로 흐려 보내도 5km 격자는 대개 그대로다 — 날씨가 흔들리지 않는다."""
    assert latlon_to_grid(35.18, 129.08) == (98, 76)


@pytest.mark.parametrize(("lat", "lon"), [(91.0, 127.0), (37.0, 181.0)])
def test_범위_밖이면_거절하고_좌표를_싣지_않는다(lat, lon):
    with pytest.raises(ValueError) as exc:
        latlon_to_grid(lat, lon)
    assert str(lat) not in str(exc.value)
