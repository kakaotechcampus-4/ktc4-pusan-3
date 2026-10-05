"""날씨 원문 계약. 날씨 어댑터(`app/integrations/`)가 싣고 Activity 가 읽는 모양.

`app/integrations/` 는 core 만 import 할 수 있어서 어댑터와 Agent 가 함께 보는 모양은 여기 둔다
(`core/event_draft.py` 와 같은 자리). 값은 전부 API 원문 문자열이다 — 어댑터는 호출 · 타임아웃 ·
지금 시각 칸 고르기까지만 하고 해석하지 않는다. "-" · NO_DATA · 강수 문자열을 읽는 것은
Activity `weather.py` 가 테스트와 함께 맡는다 (D8).

🚨 좌표는 여기 없다. 날씨 쪽으로는 기상청 5km 격자(`WeatherGrid`)만 넘어온다 (4-3).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WeatherGrid:
    """기상청 5km 격자. 날씨 포트는 좌표 대신 이 값만 받는다."""

    nx: int
    ny: int


@dataclass(frozen=True)
class RawForecast:
    """단기예보(`getVilageFcst`) 지금 시각 칸의 `fcstValue` 원문."""

    sky: str | None  # SKY — "1" · "3" · "4"
    pcp: str | None  # PCP — "강수없음" · "1.0mm 미만" · "0" …
    pop: str | None  # POP — "0" ~ "100"


@dataclass(frozen=True)
class RawAir:
    """에어코리아 측정소별 실시간(`getMsrstnAcctoRltmMesureDnsty`) 최근 한 시각의 원문."""

    pm10: str | None  # pm10Value — 측정기가 멈추면 "-"
    pm10_flag: str | None  # pm10Flag — 정상이면 None, 멈추면 "통신장애" 등
    pm25: str | None
    pm25_flag: str | None
    o3: str | None
    o3_flag: str | None


@dataclass(frozen=True)
class RawAdvisoryRow:
    """특보코드조회(`getPwnCd`) 한 행. 숫자로 와도 문자열로 바꿔 싣는다."""

    area_code: str  # areaCode — 특보구역
    warn_var: str  # warnVar — 특보 종류
    warn_stress: str  # warnStress — 주의보 · 경보 · 중대경보
    command: str  # command — 발표 · 해제 · 연장 …
    tm_fc: str  # tmFc — 발표 시각 YYYYMMDDHHMM
    tm_seq: str  # tmSeq — 같은 시각이면 순번으로 가른다


@dataclass(frozen=True)
class RawAdvisories:
    result_code: str  # header.resultCode 그대로. "03"(NO_DATA)도 실패로 올리지 않는다
    rows: tuple[RawAdvisoryRow, ...] = ()
