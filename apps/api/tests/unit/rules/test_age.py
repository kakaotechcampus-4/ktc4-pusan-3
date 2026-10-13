"""월령·단계 계산 규칙.

경계마다 양쪽을 본다.
필수 월령: 3/4 · 11/12 · 17/18 · 23/24 · 35/36 · 47/48 · 71/72
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.rules.age import (
    LifeStage,
    age_display,
    first_month_of,
    life_stage,
    months_between,
    stage_of,
)

KST = timezone(timedelta(hours=9))

# 경계는 `reference/age_gates.yaml` 이 정하고 이 함수들은 인자로 받는다 (#372).
# 여기서는 파일과 따로 값을 적어 두어, 함수가 받은 경계로만 가르는지 본다.
BOUNDARIES = (
    (0, "infant_milk"),
    (4, "infant_weaning"),
    (12, "toddler"),
    (36, "preschool"),
)


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
        assert stage_of(months, BOUNDARIES) == stage

    def test_음수_월령은_거절(self):
        with pytest.raises(ValueError, match="월령은 음수일 수 없다"):
            stage_of(-1, BOUNDARIES)

    @pytest.mark.parametrize(
        ("months", "band"),
        [(0, "infant"), (11, "infant"), (12, "toddler"), (36, "toddler")],
    )
    def test_영아기_유아기(self, months, band):
        birth = date(2020, 1, 1)
        today = date(2020 + months // 12, 1 + months % 12, 1)
        assert life_stage(birth, today, BOUNDARIES).big == band


class TestAgeDisplay:
    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            (date(2026, 1, 15), "생후 1일"),  # 태어난 날 = 1일 (기념일식)
            (date(2026, 4, 14), "생후 90일"),  # 3개월 전날
            (date(2026, 4, 15), "3개월"),
            (date(2028, 1, 14), "23개월"),  # 24개월 전날
            (date(2028, 1, 15), "만 2세"),  # 24개월
            (date(2030, 1, 14), "만 3세"),  # 만 4세 전날
        ],
    )
    def test_경계_양쪽(self, today, expected):
        assert age_display(date(2026, 1, 15), today) == expected


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
        assert life_stage(date(2025, 9, 22), today, BOUNDARIES).months == 12


class TestLifeStageResult:
    def test_얼어_있다(self):
        result = life_stage(date(2025, 9, 22), date(2026, 9, 22), BOUNDARIES)
        assert result == LifeStage(months=12, stage="toddler", big="toddler")
        with pytest.raises(AttributeError):
            result.months = 13  # type: ignore[misc]


class TestFirstMonthOf:
    """월령을 모르고 단계만 알 때 쓰는 값 — 그 단계에서 가장 어린 월령."""

    @pytest.mark.parametrize(
        ("stage", "months"),
        [("infant_milk", 0), ("infant_weaning", 4), ("toddler", 12), ("preschool", 36)],
    )
    def test_단계가_시작하는_월령(self, stage, months):
        assert first_month_of(stage, BOUNDARIES) == months
        assert stage_of(first_month_of(stage, BOUNDARIES), BOUNDARIES) == stage

    def test_모르는_단계는_ValueError(self):
        with pytest.raises(ValueError, match="모르는 단계"):
            first_month_of("teen", BOUNDARIES)  # type: ignore[arg-type]


class TestBoundariesAreArguments:
    """숫자는 인자가 정한다 — 함수 안에 월령이 남아 있으면 파일을 바꿔도 단계가 안 바뀐다."""

    OTHER = (
        (0, "infant_milk"),
        (6, "infant_weaning"),
        (18, "toddler"),
        (48, "preschool"),
    )

    @pytest.mark.parametrize(
        ("months", "stage"),
        [(5, "infant_milk"), (6, "infant_weaning"), (17, "infant_weaning"), (18, "toddler")],
    )
    def test_stage_of_는_받은_경계로_가른다(self, months, stage):
        assert stage_of(months, self.OTHER) == stage

    def test_life_stage_는_받은_경계를_쓴다(self):
        result = life_stage(date(2025, 3, 22), date(2026, 3, 22), self.OTHER)  # 12개월
        assert result.stage == "infant_weaning"
        assert result.big == "infant"

    def test_first_month_of_는_받은_경계를_쓴다(self):
        assert first_month_of("toddler", self.OTHER) == 18
