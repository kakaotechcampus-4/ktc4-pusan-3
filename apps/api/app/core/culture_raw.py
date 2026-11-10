"""문화시설 · 관광지 원문 계약. TourAPI 어댑터(`app/integrations/tour/events.py`)가 싣고 Activity
`culture.py` 가 읽는 모양 (`event_raw.py` 와 같은 자리).

한국관광공사 TourAPI 지역기반 관광정보(`areaBasedList2`)의 문화시설(14) · 관광지(12) 한 행이다 —
박물관 · 과학관 · 미술관 · 체험관을 고르는 데 쓴다. 값은 전부 API 원문 문자열이다. 이름으로 아이와
갈 곳을 거르고 거리를 계산하는 것은 Activity 가 테스트와 함께 맡는다.

🚨 칸이 곧 화이트리스트다. 전화번호 · 주소 · 홈페이지 · 이미지 · 요금은 응답에 있어도 칸이 없어서
   어댑터 밖으로 나오지 못한다.
🚨 위치는 여기 없다. 어댑터는 시 · 도 이름만 받고 그 시 · 도 목록을 넘긴다 — 거리는 서버 안에서
   Activity 가 계산한다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RawSite:
    """`areaBasedList2` 한 행의 원문."""

    content_id: str  # contentid
    title: str
    mapx: str  # 경도
    mapy: str  # 위도
    content_type: str  # contenttypeid — 14 문화시설 · 12 관광지
    category: str  # lclsSystm2 — VE07 전시시설 · EX06 체험관광 …
