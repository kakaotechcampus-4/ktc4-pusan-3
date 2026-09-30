"""날씨 판정 — 설계 4-2 표를 코드로 옮긴 것.

모델에게는 **판정(`outdoor_ok`)과 등급 라벨만** 준다. 수치를 주면 모델이 자기 기준으로 다시 읽어
"나쁨이지만 잠깐이면 괜찮아요"를 쓴다. 화면에 붙는 안내는 코드 상수다.

임계는 출처가 이미 정한 경계를 그대로 옮겼다. 특보는 기준 수치 대신 발효 여부만 본다 — 기준이
개정되면 자동으로 따라가게 하려는 것이다. 관례값은 강수확률 60% 하나이고 eval 로 조정한다.

🚨 실패를 기본값으로 메우지 않는다. 예보 실패는 "맑음"이 아니고 미세먼지 실패는 "좋음"이 아니다.
"""

import math
import re
from dataclasses import dataclass, field

from app.agents.activity.store.ports import (
    Advisories,
    AdvisoryLevel,
    AirQuality,
    Forecast,
    RawAdvisories,
    RawAdvisoryRow,
    RawAir,
    RawForecast,
)

# ── 임계 (4-2) ──────────────────────────────────────────────────────────────
# 에어코리아 통합 예보 등급 경계 (㎍/㎥). 나쁨은 경고, 매우나쁨은 차단
PM10_BAD = 81
PM10_VERY_BAD = 151
PM25_BAD = 36
PM25_VERY_BAD = 76
# 오존 주의보 · 경보 발령 기준 (1시간 평균 ppm)
OZONE_ADVISORY_PPM = 0.12
OZONE_WARNING_PPM = 0.30
# 기상청이 "보통 비" · "강한 비" 라고 부르기 시작하는 1시간 강수량 (mm)
RAIN_MODERATE_MM = 3.0
RAIN_HEAVY_MM = 15.0
# 유일한 관례값 — eval 로 조정한다
POP_CAUTION_PERCENT = 60

# 기상청 자외선지수 등급 (아래 끝, 등급). API 는 지수만 주고 등급은 주지 않는다 —
# 경계는 기상청 표를 옮겨 적은 것이고 우리가 정한 값이 아니다
UV_GRADE_FLOORS: tuple[tuple[int, str], ...] = (
    (11, "위험"),
    (8, "매우높음"),
    (6, "높음"),
    (3, "보통"),
    (0, "낮음"),
)

# ── 화면 문구 키 ────────────────────────────────────────────────────────────
WEATHER_UNCHECKED = "weather.unchecked"
AIR_UNCHECKED = "weather.air_unchecked"
UV_UNCHECKED = "weather.uv_unchecked"
OUTDOOR_BLOCKED = "weather.outdoor_blocked"
AFTER_SUNSET = "weather.after_sunset"
HEAT_ADVISORY = "weather.heat_advisory"
COLD_ADVISORY = "weather.cold_advisory"
AIR_BAD = "weather.air_bad"
OZONE_ADVISORY = "weather.ozone_advisory"
UV_VERY_HIGH = "weather.uv_very_high"
RAIN_LIKELY = "weather.rain_likely"

NOTICES: dict[str, str] = {
    WEATHER_UNCHECKED: "날씨를 확인하지 못해 실내 놀이만 골랐어요.",
    AIR_UNCHECKED: "미세먼지는 확인하지 못했어요.",
    UV_UNCHECKED: "자외선은 확인하지 못했어요.",
    OUTDOOR_BLOCKED: "오늘은 바깥 활동이 어려운 날이라 실내 놀이만 골랐어요.",
    AFTER_SUNSET: "해가 져서 실내 놀이만 골랐어요.",
    HEAT_ADVISORY: "폭염주의보가 내려져 있어요. 한낮은 피해 주세요.",
    COLD_ADVISORY: "한파주의보가 내려져 있어요. 따뜻하게 입혀 주세요.",
    AIR_BAD: "미세먼지가 나빠요. 바깥 활동은 짧게 해 주세요.",
    OZONE_ADVISORY: "오존이 높아요. 오후 바깥 활동은 짧게 해 주세요.",
    UV_VERY_HIGH: "자외선이 매우 높아요. 그늘과 모자를 챙겨 주세요.",
    RAIN_LIKELY: "비가 올 수 있어요. 우산을 챙겨 주세요.",
}

# 야외를 막아도 남겨야 하는 안내 — 무엇을 확인 못 했는지는 막혀도 알린다
_UNCHECKED = frozenset({AIR_UNCHECKED, UV_UNCHECKED})


@dataclass(frozen=True)
class WeatherBrief:
    """판정 결과. 모델에게는 `to_model_payload()` 만 간다."""

    outdoor_ok: bool
    labels: dict[str, str] = field(default_factory=dict)  # 등급 라벨. 수치는 없다
    notices: tuple[str, ...] = ()  # 화면에 붙는 문구 키 (NOTICES)

    def to_model_payload(self) -> dict[str, object]:
        return {"outdoor_ok": self.outdoor_ok, **self.labels}

    def notice_texts(self) -> tuple[str, ...]:
        return tuple(NOTICES[key] for key in self.notices)


def judge_weather(
    *,
    forecast: Forecast | None,
    air: AirQuality | None,
    uv_index: int | None,
    advisories: Advisories | None,
    after_sunset: bool,
) -> WeatherBrief:
    """조회 결과를 4-2 표로 판정한다. None 은 그 조회가 실패했다는 뜻이다.

    - 예보나 특보가 실패했거나 강수량을 못 읽었으면 실내만. 특보는 차단 신호라 예보와 같이 본다.
    - 미세먼지 · 자외선만 실패했으면 야외는 허용하고 확인 못 했다고 알린다.
    """
    if forecast is None or forecast.precip_mm_per_h is None or advisories is None:
        return WeatherBrief(outdoor_ok=False, notices=(WEATHER_UNCHECKED,))

    block = False
    notices: list[str] = []
    labels: dict[str, str] = {}

    if forecast.sky:
        labels["sky"] = forecast.sky
    rain = forecast.precip_mm_per_h
    labels["rain"] = _rain_label(rain)
    if rain >= RAIN_HEAVY_MM:
        block = True
    elif rain >= RAIN_MODERATE_MM or (
        forecast.pop_percent is not None and forecast.pop_percent >= POP_CAUTION_PERCENT
    ):
        notices.append(RAIN_LIKELY)

    if advisories.severe or "warning" in (advisories.heat, advisories.cold):
        block = True
    if advisories.heat == "advisory":
        notices.append(HEAT_ADVISORY)
    if advisories.cold == "advisory":
        notices.append(COLD_ADVISORY)

    if air is None or (air.pm10 is None and air.pm25 is None):
        notices.append(AIR_UNCHECKED)
    if air is not None:
        if air.pm10 is not None:
            labels["pm10"] = _pm_grade(air.pm10, bad=PM10_BAD, very_bad=PM10_VERY_BAD, good=30)
        if air.pm25 is not None:
            labels["pm25"] = _pm_grade(air.pm25, bad=PM25_BAD, very_bad=PM25_VERY_BAD, good=15)
        if _at_least(air.pm10, PM10_VERY_BAD) or _at_least(air.pm25, PM25_VERY_BAD):
            block = True
        elif _at_least(air.pm10, PM10_BAD) or _at_least(air.pm25, PM25_BAD):
            notices.append(AIR_BAD)
        if _at_least(air.ozone_ppm, OZONE_WARNING_PPM):
            block = True
        elif _at_least(air.ozone_ppm, OZONE_ADVISORY_PPM):
            notices.append(OZONE_ADVISORY)

    uv = uv_grade(uv_index)
    if uv is None:
        notices.append(UV_UNCHECKED)
    else:
        labels["uv"] = uv
        if uv == "위험":
            block = True
        elif uv == "매우높음":
            notices.append(UV_VERY_HIGH)

    if after_sunset or block:
        # 야외가 막히면 "우산 챙기세요" 같은 안내는 필요 없다. 확인 못 한 것만 남긴다
        kept = [key for key in notices if key in _UNCHECKED]
        notices = [*kept, AFTER_SUNSET if after_sunset else OUTDOOR_BLOCKED]
        return WeatherBrief(outdoor_ok=False, labels=labels, notices=tuple(dict.fromkeys(notices)))

    return WeatherBrief(outdoor_ok=True, labels=labels, notices=tuple(dict.fromkeys(notices)))


# ── 파서 ────────────────────────────────────────────────────────────────────
# 기상청 단기예보 1시간 강수량(PCP)은 문자열로 온다 (단기예보 조회서비스 활용가이드)
#   "강수없음" · "1.0mm 미만" · "3.0mm" · "30.0~50.0mm" · "50.0mm 이상"
# 2026-09-30 실호출: 비가 없으면 "강수없음", 발표 3일 뒤 시각부터는 "0" 으로 온다
# TODO: 비 오는 날의 구간 형식을 실호출로 확인한다 — 확인한 날은 비가 없었다
_RANGE = re.compile(r"^(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)\s*mm$")
_BELOW = re.compile(r"^(\d+(?:\.\d+)?)\s*mm\s*미만$")
_ABOVE = re.compile(r"^(\d+(?:\.\d+)?)\s*mm\s*이상$")
_AMOUNT = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:mm)?$")


def parse_precip(value: str | None) -> float | None:
    """PCP 문자열을 mm 로. 못 읽으면 None — "강수없음"(0)과 다르다.

    구간은 아래 끝을 쓴다. 구간이 판정 경계(3 · 15mm)를 가로지르지 않아서 아래 끝을 써도
    판정이 같다 — "30.0~50.0mm" 는 어느 값이든 강한 비다. "1.0mm 미만" 은 0 과 1 의 가운데로 둔다.
    """
    if value is None:
        return None
    text = value.strip()
    if text == "강수없음":
        return 0.0
    if match := _BELOW.match(text):
        return float(match.group(1)) / 2
    if match := _RANGE.match(text):
        return float(match.group(1))
    if match := _ABOVE.match(text):
        return float(match.group(1))
    if match := _AMOUNT.match(text):
        return float(match.group(1))
    return None


def parse_pop(value: str | None) -> int | None:
    """강수확률(POP) 문자열을 정수 %로. 0–100 밖이거나 못 읽으면 None."""
    if value is None or not value.strip().isdigit():
        return None
    number = int(value.strip())
    return number if 0 <= number <= 100 else None


def parse_uv(value: str | None) -> int | None:
    """자외선지수 문자열을 정수로. 3시간 간격 칸(h0 · h3 · …)의 먼 시각은 "" 로 온다 — None."""
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())


def uv_grade(index: int | None) -> str | None:
    """지수를 기상청 등급으로. 모르거나 음수면 None — "낮음"으로 채우지 않는다."""
    if index is None or index < 0:
        return None
    return next(grade for floor, grade in UV_GRADE_FLOORS if index >= floor)


def parse_air(value: str | None, flag: str | None = None) -> float | None:
    """에어코리아 측정값 문자열을 숫자로.

    측정기가 멈추면 값이 "-" 로 오고 `pm10Flag` 같은 칸에 사유("통신장애" 등)가 붙는다.
    사유가 붙었거나 숫자가 아니면 None — 0 으로 읽으면 고장 난 날이 "좋음"이 된다.
    """
    if flag or value is None:
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


# ── 원문 → 판정 입력 ────────────────────────────────────────────────────────
# 포트는 원문 문자열을 준다. 어댑터(app/integrations)가 이 파일을 import 할 수 없어서
# 해석은 여기 한 곳에서만 한다.

# 단기예보 하늘상태(SKY) 코드 (활용가이드). 실응답에서 "1" · "3" 을 봤다
SKY_LABELS: dict[str, str] = {"1": "맑음", "3": "구름많음", "4": "흐림"}

# 특보코드조회(getPwnCd) 코드 (활용가이드). 실응답에서 호우 "2" · 발표 "1" · 해제 "2" 를 봤다
KMA_OK = "00"
KMA_NO_DATA = "03"  # 발효 중인 특보가 없다. 실패가 아니다
_WARN_HEAT = "12"  # 폭염
_WARN_COLD = "3"  # 한파
_WARN_SEVERE = frozenset({"1", "2", "7", "8"})  # 강풍 · 호우 · 태풍 · 대설
_IN_EFFECT = frozenset({"1", "3", "6", "7"})  # 발표 · 연장 · 정정 · 변경발표
_CLEARED = frozenset({"2", "8"})  # 해제 · 변경해제
_STRESS: dict[str, AdvisoryLevel] = {
    "0": "advisory",
    "1": "warning",
    "2": "warning",
}  # 2 = 중대경보


def read_forecast(raw: RawForecast) -> Forecast:
    return Forecast(
        sky=SKY_LABELS.get((raw.sky or "").strip()),
        precip_mm_per_h=parse_precip(raw.pcp),
        pop_percent=parse_pop(raw.pop),
    )


def read_air(raw: RawAir) -> AirQuality:
    return AirQuality(
        pm10=parse_air(raw.pm10, raw.pm10_flag),
        pm25=parse_air(raw.pm25, raw.pm25_flag),
        ozone_ppm=parse_air(raw.o3, raw.o3_flag),
    )


def read_advisories(raw: RawAdvisories) -> Advisories | None:
    """특보 행을 발효 여부로. None 은 "모른다"이고 judge_weather 가 실내만으로 처리한다.

    - `03 NO_DATA` 는 발효 중인 특보가 없다는 뜻이다. 그 밖의 결과 코드는 실패다.
    - 발표와 해제가 따로 한 행씩 온다. (구역, 종류)마다 가장 최근 행의 command 로 본다.
    - 모르는 command · 등급이 있으면 None — 모르는 특보를 "없음"으로 넘기지 않는다.
    """
    code = raw.result_code.strip()
    if code == KMA_NO_DATA:
        return Advisories(heat="none", cold="none", severe=False)
    if code != KMA_OK:
        return None

    latest: dict[tuple[str, str], RawAdvisoryRow] = {}
    for row in raw.rows:
        key = (row.area_code.strip(), row.warn_var.strip())
        if key not in latest or _issued(row) > _issued(latest[key]):
            latest[key] = row

    heat: AdvisoryLevel = "none"
    cold: AdvisoryLevel = "none"
    severe = False
    for (_, warn_var), row in latest.items():
        command = row.command.strip()
        if command in _CLEARED:
            continue
        level = _STRESS.get(row.warn_stress.strip())
        if command not in _IN_EFFECT or level is None:
            return None
        if warn_var == _WARN_HEAT:
            heat = _stronger(heat, level)
        elif warn_var == _WARN_COLD:
            cold = _stronger(cold, level)
        elif warn_var in _WARN_SEVERE:
            severe = True
    return Advisories(heat=heat, cold=cold, severe=severe)


def _issued(row: RawAdvisoryRow) -> tuple[str, int]:
    seq = row.tm_seq.strip()
    return row.tm_fc.strip(), int(seq) if seq.isdigit() else -1


def _stronger(current: AdvisoryLevel, new: AdvisoryLevel) -> AdvisoryLevel:
    order = ("none", "advisory", "warning")
    return new if order.index(new) > order.index(current) else current


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _pm_grade(value: float, *, good: int, bad: int, very_bad: int) -> str:
    if value >= very_bad:
        return "매우나쁨"
    if value >= bad:
        return "나쁨"
    return "좋음" if value <= good else "보통"


def _rain_label(mm: float) -> str:
    if mm <= 0:
        return "비 없음"
    if mm >= RAIN_HEAVY_MM:
        return "강한 비"
    if mm >= RAIN_MODERATE_MM:
        return "보통 비"
    return "약한 비"
