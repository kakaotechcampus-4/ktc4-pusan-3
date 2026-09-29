"""날씨 판정 — 설계 4-2 표를 코드로 옮긴 것.

모델에게는 **판정(`outdoor_ok`)과 등급 라벨만** 준다. 수치를 주면 모델이 자기 기준으로 다시 읽어
"나쁨이지만 잠깐이면 괜찮아요"를 쓴다. 화면에 붙는 안내는 코드 상수다.

임계는 출처가 이미 정한 경계를 그대로 옮겼다. 특보는 기준 수치 대신 발효 여부만 본다 — 기준이
개정되면 자동으로 따라가게 하려는 것이다. 관례값은 강수확률 60% 하나이고 eval 로 조정한다.

🚨 실패를 기본값으로 메우지 않는다. 예보 실패는 "맑음"이 아니고 미세먼지 실패는 "좋음"이 아니다.
"""

import re
from dataclasses import dataclass, field

from app.agents.activity.store.ports import Advisories, AirQuality, Forecast

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

# 기상청 자외선 지수 등급. API 가 준 등급을 그대로 쓰고 다시 분류하지 않는다
UV_GRADES = ("낮음", "보통", "높음", "매우높음", "위험")

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
    uv_grade: str | None,
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

    if uv_grade not in UV_GRADES:
        notices.append(UV_UNCHECKED)
    else:
        labels["uv"] = uv_grade
        if uv_grade == "위험":
            block = True
        elif uv_grade == "매우높음":
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
# TODO: 실호출로 형식을 한 번 더 확인한다 — 2026-09-29 기준 키가 이 API 에 열리지 않아 403
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


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _pm_grade(value: int, *, good: int, bad: int, very_bad: int) -> str:
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
