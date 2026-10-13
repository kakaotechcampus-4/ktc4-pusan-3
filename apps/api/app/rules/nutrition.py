"""영양 판정의 순수 규칙. 정본은 `docs/agents/food/영양소_계산_설계.md` §4 ~ §6.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"100% 맞아야 하는 것은 코드" 라서 구간을 정하는 일은 전부 여기서 한다 (루트 CLAUDE.md §3).

이 모듈은 숫자만 받는다. 기준값(EAR · AMDR · 상한)이 어디서 왔는지는 모른다 — 섭취기준 표는
검수한 시드가 정하고, 어떤 영양소에 어떤 기준을 쓸지는 호출부가 고른다. 여기에는 기준값도
식품도 없다.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal

# 영양소 구간. 판정 결과에만 쓰고 저장하지 않는다 — 전날 구간도 7일 전부터 하루씩 다시 판정해서
# 얻는다(설계 §3 ④). `app/rules/age.py` 의 Band(연령대)와 다른 축이다.
NutrientBand = Literal["low", "ok", "high"]

# `nutrient_reference.age_group` 과 같은 값
KdriGroup = Literal["1-2y", "3-5y"]

# 기록 행 수 임계 — 7일에 이만큼 미만이면 수치 기반 결론을 내지 않는다 (설계 §5 · N-1).
# 하루 평균 1.5행이 기준이다. 기록량을 보고 조정한다
MIN_RECORDS = 10

# 한 번 low 였던 항목이 ok 로 돌아가려면 EAR 의 이 배수를 넘어야 한다 (설계 §6 · N-8 임시값)
HYSTERESIS_FACTOR = 1.1

# `amount_known=false` 인 행이 부족·과잉 판정에 들어가는 비율 (설계 §4). 식품군 출현에는 그대로 센다
UNKNOWN_AMOUNT_WEIGHT = 0.5

_FIRST_KDRI_MONTH = 12  # 돌 전 영양소 분석은 지원하지 않는다
_KDRI_3_5Y_FROM = 36  # Food_Tool_명세 §1: 12–35 `1-2y` / 36+ `3-5y`


def select_kdri_group(months: int) -> KdriGroup:
    """월령으로 섭취기준 연령군을 고른다.

    12개월 미만은 연령군이 없다. 영아기 영양소 분석은 registry 가 모델을 부르기 전에 닫지만,
    그 방어가 풀려도 1-2세 기준을 돌 전 아이에게 대신 쓰지 않는다.
    """
    if months < _FIRST_KDRI_MONTH:
        raise ValueError(f"섭취기준 연령군은 12개월부터다: months={months}")
    return "3-5y" if months >= _KDRI_3_5Y_FROM else "1-2y"


def is_insufficient(records: int, *, minimum: int = MIN_RECORDS) -> bool:
    """기록 행이 모자라 수치 기반 결론을 낼 수 없는가. 끼니 비율이 아니라 행 수로 본다."""
    return records < minimum


def amount_weight(factor: float, *, known: bool) -> float:
    """한 행이 부족·과잉 판정에 넣는 양. 양을 모르면 계수의 절반만 들어간다 (설계 §4).

    안 먹음(0.0)은 양을 몰라도 0 이다.
    """
    return factor if known else factor * UNKNOWN_AMOUNT_WEIGHT


def mean_over_recorded_days(day_totals: Mapping[date, float]) -> float | None:
    """기록이 있는 날들의 일평균. 기록이 없는 날은 0 이 아니라 분석에서 뺀다 (설계 §1-3).

    `day_totals` 에 키가 있는 날이 기록한 날이다. 합이 0 이어도(안 먹음) 그날은 센다.
    기록한 날이 없으면 None — 평균이 없는 것이지 0 이 아니다.
    """
    if not day_totals:
        return None
    return sum(day_totals.values()) / len(day_totals)


def settle_ear_band(daily_means: Sequence[float], ear: float) -> Literal["low", "ok"] | None:
    """EAR 기준 부족 여부. 날짜 순서대로 일평균을 하나씩 판정해 마지막 구간을 돌려준다.

    한 번 low 로 판정된 항목은 EAR 의 `HYSTERESIS_FACTOR` 배를 **넘어야** ok 로 돌아간다 —
    경계에서 매일 결론이 뒤집히면 신뢰를 잃는다. ok 였던 항목은 EAR 미만이면 바로 low 다.
    첫 날은 직전 구간 없이 EAR 로만 판정한다. 저장한 판정은 없고 7일 전부터 하루씩 다시
    판정해서 전날 구간을 얻는다 (설계 §3 ④).

    EAR 이 0 이하면 판정하지 않는다 — 비었거나 깨진 기준값을 0 으로 읽으면 모두 ok 가 된다.
    """
    if ear <= 0:
        raise ValueError(f"EAR 은 0 보다 커야 한다: {ear}")
    band: Literal["low", "ok"] | None = None
    for mean in daily_means:
        recover_at = ear * HYSTERESIS_FACTOR if band == "low" else ear
        # low 에서는 recover_at 을 넘어야 돌아온다. 그 밖에는 EAR 이상이면 ok
        stays_low = mean <= recover_at if band == "low" else mean < recover_at
        band = "low" if stays_low else "ok"
    return band


def band_range(mean: float, low: float, high: float) -> NutrientBand:
    """AMDR 처럼 범위가 있는 항목 — 범위 밖이면 low 또는 high (설계 §6)."""
    if high < low:
        raise ValueError(f"범위가 뒤집혔다: low={low} high={high}")
    if mean < low:
        return "low"
    if mean > high:
        return "high"
    return "ok"


def band_upper(mean: float, limit: float) -> Literal["ok", "high"]:
    """나트륨 · 당류 · 포화지방처럼 상한만 있는 항목. 상한을 넘으면 high 이고 low 는 없다 (설계 §6).

    "상한 근접" 은 설계에 기준이 없어 구현하지 않았다 — 넘었을 때만 high 다.
    """
    return "high" if mean > limit else "ok"


def energy_share(grams: float, kcal_per_gram: float, total_kcal: float) -> float | None:
    """한 영양소가 총열량에서 차지하는 비율(%kcal). 총열량이 0 이하면 None.

    AMDR 은 %kcal 이라 그램 합계를 열량으로 바꿔 비교한다. 계수는 호출부가 넘긴다
    (탄수화물 · 단백질 4, 지방 9 처럼 일반 열량 환산계수).
    """
    if total_kcal <= 0:
        return None
    return grams * kcal_per_gram / total_kcal * 100
