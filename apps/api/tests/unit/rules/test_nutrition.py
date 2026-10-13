"""영양 판정의 순수 규칙 — `영양소_계산_설계.md` §4 ~ §6 와 §8 테스트 표.

DB·LLM 없이 숫자만 넣고 구간이 나오는지 본다. 기준값(EAR 등)은 이 파일에서 정한 가짜 숫자다 —
실제 섭취기준 표는 검수한 시드가 정한다.
"""

from datetime import date

import pytest

from app.rules.nutrition import (
    HYSTERESIS_FACTOR,
    MIN_RECORDS,
    UNKNOWN_AMOUNT_WEIGHT,
    amount_weight,
    band_range,
    band_upper,
    energy_share,
    is_insufficient,
    mean_over_recorded_days,
    select_kdri_group,
    settle_ear_band,
)

EAR = 100.0


# ── select_kdri_group — 섭취기준 연령군 ──────────────────────────────
@pytest.mark.parametrize(
    ("months", "expected"),
    [(12, "1-2y"), (23, "1-2y"), (35, "1-2y"), (36, "3-5y"), (60, "3-5y"), (71, "3-5y")],
)
def test_연령군은_12에서_35개월이_1_2세_36개월부터가_3_5세(months: int, expected: str) -> None:
    assert select_kdri_group(months) == expected


@pytest.mark.parametrize("months", [0, 4, 11])
def test_돌_전에는_연령군이_없다(months: int) -> None:
    # 영아기 영양소 분석은 지원하지 않는다 — 12개월 미만 기준값을 1-2세로 대신하지 않는다
    with pytest.raises(ValueError):
        select_kdri_group(months)


# ── is_insufficient — 기록 행 수 ─────────────────────────────────────
def test_기록이_10행_미만이면_부족하다() -> None:
    assert MIN_RECORDS == 10
    assert is_insufficient(9) is True
    assert is_insufficient(10) is False


def test_7일_8행은_부족하다() -> None:
    # §8 "7일 8행 → insufficient=True"
    assert is_insufficient(8) is True


def test_기록이_0행이어도_부족이다() -> None:
    assert is_insufficient(0) is True


# ── amount_weight — 양을 모르는 행은 절반 가중 ────────────────────────
def test_양을_아는_행은_계수_그대로_들어간다() -> None:
    assert amount_weight(0.5, known=True) == 0.5


def test_양을_모르는_행은_계수의_절반만_들어간다() -> None:
    # 급식(메뉴만) = 1.0 / known=false → 절반 가중
    assert UNKNOWN_AMOUNT_WEIGHT == 0.5
    assert amount_weight(1.0, known=False) == 0.5
    # "남김(양 불명)" = 0.5 / known=false
    assert amount_weight(0.5, known=False) == 0.25


def test_안_먹은_행은_양을_몰라도_0이다() -> None:
    assert amount_weight(0.0, known=False) == 0.0


# ── mean_over_recorded_days — 결측은 0이 아니다 ───────────────────────
def test_기록이_없는_날은_평균에서_빠진다() -> None:
    # 7일 중 이틀만 기록 — 5일을 0으로 넣으면 평균이 3분의 1 이하로 내려가 거짓 low 가 된다
    totals = {date(2026, 10, 1): 60.0, date(2026, 10, 3): 80.0}
    assert mean_over_recorded_days(totals) == 70.0


def test_기록한_날이_하나도_없으면_평균이_없다() -> None:
    assert mean_over_recorded_days({}) is None


def test_기록한_날의_합이_0이어도_그날은_센다() -> None:
    # 안 먹음(0.0) 도 기록이다 — 기록이 없는 날과 다르다
    totals = {date(2026, 10, 1): 0.0, date(2026, 10, 2): 100.0}
    assert mean_over_recorded_days(totals) == 50.0


# ── settle_ear_band — EAR 대비 부족 + 히스테리시스 ────────────────────
def test_평균이_EAR_미만이면_low_이상이면_ok() -> None:
    assert settle_ear_band([99.9], EAR) == "low"
    assert settle_ear_band([100.0], EAR) == "ok"


def test_한_번_low_면_EAR의_1_1배를_넘어야_ok_로_돌아간다() -> None:
    assert HYSTERESIS_FACTOR == 1.1
    # 첫날 low, 다음 날 EAR 은 넘었지만 1.1배는 안 넘음 → 계속 low
    assert settle_ear_band([90.0, 105.0], EAR) == "low"
    # 1.1배 "초과" 여야 돌아온다. 정확히 같으면 아직 low
    assert settle_ear_band([90.0, 110.0], EAR) == "low"
    assert settle_ear_band([90.0, 110.1], EAR) == "ok"


def test_경계에서_low_ok_low_로_흔들리지_않는다() -> None:
    # §8 "경계 진동 — EAR 근처 값이 연속 3일 low→ok→low 로 바뀌지 않음"
    assert settle_ear_band([95.0, 102.0, 97.0], EAR) == "low"
    # 히스테리시스 없이 매일 따로 판정하면 low, ok, low 로 뒤집힌다
    assert [settle_ear_band([v], EAR) for v in (95.0, 102.0, 97.0)] == ["low", "ok", "low"]


def test_ok_였던_항목은_EAR_미만으로_내려가면_바로_low() -> None:
    # 히스테리시스는 low 에서 돌아올 때만 적용된다
    assert settle_ear_band([120.0, 99.0], EAR) == "low"


def test_7일_내내_EAR_와_1_1배_사이면_첫날을_직전_구간_없이_판정해_ok() -> None:
    # §8 "일평균이 7일 내내 EAR ~ 1.1배 사이 → ok — 첫날을 직전 구간 없이 판정한다"
    assert settle_ear_band([105.0] * 8, EAR) == "ok"


def test_low_로_시작해_1_1배를_넘은_뒤에는_다시_내려가도_EAR_위면_ok() -> None:
    assert settle_ear_band([90.0, 115.0, 105.0], EAR) == "ok"


def test_구간을_정할_날이_하나도_없으면_구간이_없다() -> None:
    assert settle_ear_band([], EAR) is None


def test_EAR_가_0_이하이면_판정하지_않는다() -> None:
    # 기준값이 비었거나 깨진 것을 0 으로 읽으면 모든 아이가 ok 가 된다
    with pytest.raises(ValueError):
        settle_ear_band([50.0], 0.0)


# ── band_range — AMDR ────────────────────────────────────────────────
def test_AMDR_은_범위_밖이면_low_또는_high() -> None:
    assert band_range(44.9, 45.0, 65.0) == "low"
    assert band_range(45.0, 45.0, 65.0) == "ok"
    assert band_range(65.0, 45.0, 65.0) == "ok"
    assert band_range(65.1, 45.0, 65.0) == "high"


def test_AMDR_범위가_뒤집히면_판정하지_않는다() -> None:
    with pytest.raises(ValueError):
        band_range(50.0, 65.0, 45.0)


# ── band_upper — 나트륨 · 당류 · 포화지방 ─────────────────────────────
def test_상한_항목은_상한을_넘으면_high_아니면_ok() -> None:
    assert band_upper(1200.0, 1200.0) == "ok"
    assert band_upper(1200.1, 1200.0) == "high"


def test_상한_항목에는_low_가_없다() -> None:
    assert band_upper(0.0, 1200.0) == "ok"


# ── energy_share — 에너지 구성비 ──────────────────────────────────────
def test_에너지_구성비는_그램에_kcal_계수를_곱해_총열량으로_나눈다() -> None:
    # 탄수화물 60g(4kcal/g)=240kcal, 총 600kcal → 40%
    assert energy_share(60.0, 4.0, 600.0) == pytest.approx(40.0)


def test_총열량이_0_이하이면_구성비가_없다() -> None:
    assert energy_share(60.0, 4.0, 0.0) is None
