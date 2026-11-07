"""행사 원문 계약. 행사 어댑터(`app/integrations/`)가 싣고 Activity 가 읽는 모양.

한국관광공사 TourAPI 축제공연행사(`searchFestival2`) · 소개정보(`detailIntro2`) · 공통정보의 개요
(`detailCommon2`)다.
`app/integrations/` 는 core 만 import 할 수 있어서 어댑터와 Agent 가 함께 보는 모양은 여기
둔다(`weather_raw.py` 와 같은 자리). 값은 전부 API 원문 문자열이다 — 날짜 · 좌표 · 관람 연령을
읽고 거르는 것은 Activity `events.py` 가 테스트와 함께 맡는다.

🚨 칸이 곧 화이트리스트다. 요금 · 전화번호 · 홈페이지 · 이미지는 응답에 있어도 이 모양에 칸이 없어서
   어댑터 밖으로 나오지 못한다 — 모델에게 링크 · 가격 · 전화번호를 주지 않는다 (activity prompt).
🚨 위치는 여기 없다. 어댑터는 보호자 위치를 받지 않고 전국 목록을 넘긴다 — 거리는 서버 안에서
   Activity 가 계산한다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RawEvent:
    """`searchFestival2` 한 행의 원문."""

    content_id: str  # contentid — 소개정보를 물을 때 쓴다
    title: str
    start: str  # eventstartdate — "20261101"
    end: str  # eventenddate
    mapx: str  # 경도
    mapy: str  # 위도
    kind_code: str  # lclsSystm2 — EV01 축제 · EV02 공연 · EV03 행사
    progress: str  # progresstype — "온라인" 이면 갈 곳이 없다. 대개 비어 있다
    # lDongRegnCd — 법정동 시도 코드(부산 26 · 세종 36110). 시 · 도만 말한 행사 검색이 쓴다
    region_code: str = ""


@dataclass(frozen=True)
class RawEventIntro:
    """`detailIntro2`(contentTypeId=15) 에서 쓰는 칸만."""

    age_limit: str  # agelimit — "전 연령" · "만 13세 이상" · "" 처럼 자유 문장이다
    place: str  # eventplace — 행사 장소. 비어 있을 수 있다
    # 열리는 날을 읽는 칸 — 시작일 · 종료일은 전체 기간이라 "매주 토요일" · "월요일 휴무" 는 여기와
    # 개요에만 있다 (Activity `event_schedule.py`)
    playtime: str = ""  # playtime — 운영 시간. "(토) 19:00" · "화~일 10:00~18:00" 같은 자유 문장
    program: str = ""  # program — 행사 프로그램. 장소마다 날짜가 다른 행사는 여기 적는다
