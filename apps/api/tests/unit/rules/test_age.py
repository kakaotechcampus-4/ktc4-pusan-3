"""월령·단계 계산 규칙.

경계마다 양쪽을 본다.
필수 월령: 3/4 · 11/12 · 17/18 · 23/24 · 35/36 · 47/48 · 71/72
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.rules.age import (
    CORRECTED_UNTIL_MONTHS,
    LifeStage,
    life_stage,
    months_between,
    stage_of,
)

KST = timezone(timedelta(hours=9))


class TestMonthsBetween:
    def test_생일_당일에_월령이_오른다(self):
        birth = date(2025, 3, 12)
        assert months_between(birth, date(2026, 3, 11)) == 11
        assert months_between(birth, date(2026, 3, 12)) == 12

    def test_태어난_날은_0개월(self):
        assert months_between(date(2026, 9, 22), date(2026, 9, 22)) == 0

    @pytest.mark.parametrize(
        ("birth", "today", "expected"),
        [
            # 민법 §160③ — 해당일이 없으면 그 달의 말일이 그날이다
            (date(2026, 1, 31), date(2026, 2, 27), 0),  # 아직 말일 전
            (date(2026, 1, 31), date(2026, 2, 28), 1),  # 평년 2월 말일 = 1개월
            (date(2024, 1, 31), date(2024, 2, 29), 1),  # 윤년 2월 말일
            (date(2026, 3, 31), date(2026, 4, 30), 1),  # 30일까지인 달
            (date(2026, 1, 30), date(2026, 2, 28), 1),  # 30일생도 2월엔 말일이 그날
            (date(2026, 1, 29), date(2026, 2, 28), 1),
        ],
    )
    def test_말일_경계(self, birth, today, expected):
        assert months_between(birth, today) == expected

    def test_30일로_나누면_틀리는_구간(self):
        """(today - birth).days // 30 은 6년이면 두 달 앞선다."""
        birth, today = date(2020, 1, 1), date(2026, 1, 1)
        assert months_between(birth, today) == 72
        assert (today - birth).days // 30 == 73  # 회귀 대상

    def test_기준일이_생년월일보다_앞서면_거절(self):
        with pytest.raises(ValueError, match="기준일이 생년월일보다 앞선다"):
            months_between(date(2026, 9, 22), date(2026, 9, 21))


class TestStageBoundaries:
    @pytest.mark.parametrize(
        ("months", "stage"),
        [
            (0, "infant_milk"),
            (3, "infant_milk"),
            (4, "infant_weaning"),
            (11, "infant_weaning"),
            (12, "toddler"),
            (35, "toddler"),
            (36, "preschool"),
            (72, "preschool"),
        ],
    )
    def test_경계_양쪽(self, months, stage):
        assert stage_of(months) == stage

    def test_음수_월령은_거절(self):
        with pytest.raises(ValueError, match="월령은 음수일 수 없다"):
            stage_of(-1)

    @pytest.mark.parametrize(
        ("months", "band"),
        [(0, "infant"), (11, "infant"), (12, "toddler"), (36, "toddler")],
    )
    def test_영아기_유아기(self, months, band):
        birth = date(2020, 1, 1)
        today = date(2020 + months // 12, 1 + months % 12, 1)
        assert life_stage(birth, today).big == band


class TestCorrectedMonths:
    def test_만삭이면_보정하지_않는다(self):
        result = life_stage(date(2026, 3, 22), date(2026, 9, 22), gestational_weeks=39)
        assert result.months == 6
        assert result.corrected_months == 6

    def test_주수를_안_주면_보정하지_않는다(self):
        result = life_stage(date(2026, 3, 22), date(2026, 9, 22))
        assert result.corrected_months == result.months

    @pytest.mark.parametrize(
        ("weeks", "correction"),
        [
            (34, 1),  # 6주 조산 → 6 / 4.35 = 1.38 → 1개월
            (32, 2),  # 8주 → 1.84 → 2
            (28, 3),  # 12주 → 2.76 → 3
            (36, 1),  # 4주 → 0.92 → 1
        ],
    )
    def test_월_단위_반올림(self, weeks, correction):
        result = life_stage(date(2026, 3, 22), date(2026, 9, 22), gestational_weeks=weeks)
        assert result.months == 6
        assert result.corrected_months == 6 - correction

    def test_조산_34주_생후_6개월이면_교정_5개월(self):
        """34주 조산은 1개월 보정. 이유기(4~11개월) 안에 그대로 있다."""
        result = life_stage(date(2026, 3, 22), date(2026, 9, 22), gestational_weeks=34)
        assert result.corrected_months == 5
        assert result.stage == "infant_weaning"

    def test_24개월부터는_보정을_멈춘다(self):
        birth = date(2024, 9, 22)
        before = life_stage(birth, date(2026, 8, 22), gestational_weeks=32)
        after = life_stage(birth, date(2026, 9, 22), gestational_weeks=32)
        assert before.months == CORRECTED_UNTIL_MONTHS - 1
        assert before.corrected_months == before.months - 2
        assert after.months == CORRECTED_UNTIL_MONTHS
        assert after.corrected_months == after.months

    def test_교정연령은_음수가_되지_않는다(self):
        result = life_stage(date(2026, 9, 1), date(2026, 9, 22), gestational_weeks=24)
        assert result.corrected_months == 0

    @pytest.mark.parametrize("weeks", [19, 45, 0, -1])
    def test_있을_수_없는_주수는_거절(self, weeks):
        with pytest.raises(ValueError, match="다룰 수 없는 임신 주수"):
            life_stage(date(2026, 3, 22), date(2026, 9, 22), gestational_weeks=weeks)

    def test_stage_와_corrected_stage_를_따로_낸다(self):
        """조산 32주, 생후 4개월. 출생 후로는 이유기지만 교정으로는 아직 수유기다.

        연령별_Tool_전략 §6 — Food 의 이유식 시작은 교정연령을 쓴다. 호출부가 stage 를
        무심코 집으면 조산아가 이유식을 두 달 일찍 시작하게 된다.
        """
        result = life_stage(date(2026, 5, 22), date(2026, 9, 22), gestational_weeks=32)
        assert result.months == 4
        assert result.corrected_months == 2
        assert result.stage == "infant_weaning"
        assert result.corrected_stage == "infant_milk"

    def test_만삭이면_두_stage_가_같다(self):
        result = life_stage(date(2026, 5, 22), date(2026, 9, 22))
        assert result.stage == result.corrected_stage


class TestSafetyMonths:
    def test_안전_필터는_더_어린_쪽을_본다(self):
        result = life_stage(date(2026, 3, 22), date(2026, 9, 22), gestational_weeks=32)
        assert result.months == 6
        assert result.corrected_months == 4
        assert result.safety_months == 4

    def test_만삭이면_둘이_같다(self):
        result = life_stage(date(2026, 3, 22), date(2026, 9, 22))
        assert result.safety_months == result.months


class TestDateTypeGuard:
    def test_datetime_을_막는다(self):
        """datetime 은 date 의 하위 클래스라 isinstance 로 걸러지지 않는다.
        그대로 두면 timezone 이 붙은 값이 섞여 KST 새벽에만 게이트가 어긋난다."""
        moment = datetime(2026, 9, 22, 0, 5, tzinfo=KST)
        with pytest.raises(TypeError, match="date 가 와야 한다"):
            months_between(date(2025, 9, 22), moment)
        with pytest.raises(TypeError, match="date 가 와야 한다"):
            months_between(moment, date(2026, 9, 22))

    def test_문자열도_막는다(self):
        with pytest.raises(TypeError, match="date 가 와야 한다"):
            months_between("2025-09-22", date(2026, 9, 22))  # type: ignore[arg-type]

    def test_KST_자정_직후에도_게이트가_열린다(self):
        """UTC 서버에서 date.today() 를 부르면 KST 00:00~09:00 에 하루가 어긋난다.
        기준일을 호출부가 넘기므로 이 함수는 그 영향을 받지 않는다."""
        moment = datetime(2026, 9, 22, 0, 5, tzinfo=KST)
        today = moment.astimezone(KST).date()
        assert life_stage(date(2025, 9, 22), today).months == 12


class TestLifeStageResult:
    def test_얼어_있다(self):
        result = life_stage(date(2025, 9, 22), date(2026, 9, 22))
        assert result == LifeStage(
            months=12,
            corrected_months=12,
            stage="toddler",
            corrected_stage="toddler",
            big="toddler",
        )
        with pytest.raises(AttributeError):
            result.months = 13  # type: ignore[misc]
