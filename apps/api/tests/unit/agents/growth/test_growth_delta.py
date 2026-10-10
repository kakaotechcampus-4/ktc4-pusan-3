"""growth_review — compute_growth_delta 검증.

- 측정일이 **전부** 나온다. 최소 간격도 연령 축도 없다.
- 키 · 몸무게를 지표별로 센다. 한쪽만 잰 날은 그 값만 적고 빈 칸을 채우지 않는다.
- 값은 `Decimal` 이고 반올림하지 않는다.
- 요약은 방향을 동사로 말하고(늘었어요 / 줄었어요 / 그대로예요) 숫자는 크기만 적는다 (G-9).
  직전 대비 줄은 동사가 없어서 부호 그대로다.
- 판정 요청에도 판정하지 않는다 — 같은 서술에 검진 안내만 붙는다.
- 모델을 부르지 않는다(함수에 클라이언트 인자가 없다).
"""

from datetime import date

import pytest

from app.agents.growth.store.ports import GrowthMeasurement
from app.agents.growth.tools.delta import asks_judgement, compute_growth_delta
from app.rules.evaluative import find_evaluative
from tests.unit.agents.growth.support import CHILD, doc_inputs, measurement

BIRTH = date(2022, 3, 1)  # 아래 측정일들은 전부 24개월을 넘긴 뒤다
NEED_MORE = "성장폭을 보려면 측정 기록이 두 번 이상 필요해요. 최근에 재보실래요?"
CHECKUP = "성장 평가는 영유아 건강검진에서 확인해 보세요."
ONE_METRIC_WEIGHT = "몸무게는 두 번 이상 재면 변화도 함께 알려드릴게요."
ONE_METRIC_HEIGHT = "키는 두 번 이상 재면 변화도 함께 알려드릴게요."
POSTURE = (
    "두 돌 무렵에는 누워서 재다가 서서 재기 시작해서, "
    "재는 방법에 따라 키가 조금 다르게 나올 수 있어요."
)


def body(rows, *, birth=BIRTH, judgement=False) -> str:
    return compute_growth_delta(rows, birth_date=birth, asks_judgement=judgement).body


class TestBothMetrics:
    def test_측정일과_수치를_그대로_늘어놓는다(self):
        rows = [
            measurement(1, date(2026, 3, 12), "95.0", "14.0"),
            measurement(2, date(2026, 9, 12), "105.0", "17.5"),
        ]
        assert body(rows) == (
            "2026년 3월 12일부터 2026년 9월 12일까지 6개월간 키 10.0cm · 몸무게 3.5kg 늘었어요.\n"
            "  2026년 3월 12일  키 95.0cm · 몸무게 14.0kg\n"
            "  2026년 9월 12일  키 105.0cm · 몸무게 17.5kg  (직전 대비 키 10.0cm · 몸무게 3.5kg)"
        )

    def test_readout_은_코드가_쓴_성장_추이다(self):
        rows = [
            measurement(1, date(2026, 3, 12), "95.0", "14.0"),
            measurement(2, date(2026, 9, 12), "105.0", "17.5"),
        ]
        readout = compute_growth_delta(rows, birth_date=BIRTH)
        assert readout.kind == "growth_delta"
        assert readout.authored_by == "code"
        assert [ref.kind for ref in readout.source_refs] == ["child_growth_log"] * 2

    def test_측정_순서와_상관없이_시간순으로_적는다(self):
        a = measurement(1, date(2026, 3, 12), "95.0", "14.0")
        b = measurement(2, date(2026, 9, 12), "105.0", "17.5")
        assert body([b, a]) == body([a, b])

    def test_측정_6건을_전부_나열한다(self):
        rows = [
            measurement(i, date(2026, i, 10), f"{90 + i}.0", f"{13 + i}.0") for i in range(1, 7)
        ]
        text = body(rows)
        lines = text.split("\n")
        assert len(lines) == 1 + 6  # 요약 한 줄 + 측정 6줄
        for i in range(1, 7):
            assert f"2026년 {i}월 10일" in text  # 최근 2건만 쓰지 않는다
        assert "키 5.0cm · 몸무게 5.0kg" in lines[0]

    def test_2주_간격이어도_0_2cm_를_그대로_적는다(self):
        rows = [
            measurement(1, date(2026, 9, 1), "104.0", "17.0"),
            measurement(2, date(2026, 9, 15), "104.2", "17.1"),
        ]
        text = body(rows)
        assert "14일간 키 0.2cm · 몸무게 0.1kg 늘었어요." in text
        assert "말하기 어려" not in text  # 간격이 짧다고 숨기지 않는다

    def test_월령과_상관없이_같은_계산이다(self):
        rows = [
            measurement(1, date(2026, 8, 1), "70.0", "8.0"),
            measurement(2, date(2026, 9, 5), "71.0", "8.4"),
        ]
        infant = body(rows, birth=date(2025, 12, 1))
        older = body(rows, birth=date(2021, 12, 1))
        assert infant == older  # 24개월 걸침만 따로 본다 — 여기는 둘 다 같은 쪽이다


class TestDecimal:
    def test_부동소수점_오차가_없다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.5", "14.0"),
            measurement(2, date(2026, 9, 1), "104.2", "14.0"),
        ]
        text = body(rows)
        assert "키 8.7cm" in text
        assert "8.700000000000003" not in text

    def test_float_측정은_만들지_않는다(self):
        with pytest.raises(TypeError):
            GrowthMeasurement(
                id=measurement(1, date(2026, 3, 1), "95.0").id,
                child_id=CHILD,
                measured_on=date(2026, 3, 1),
                height_cm=95.5,  # type: ignore[arg-type]
                weight_kg=None,
            )

    def test_키와_몸무게가_둘_다_비면_만들지_않는다(self):
        with pytest.raises(ValueError):
            GrowthMeasurement(
                id=measurement(1, date(2026, 3, 1), "95.0").id,
                child_id=CHILD,
                measured_on=date(2026, 3, 1),
                height_cm=None,
                weight_kg=None,
            )

    def test_반올림하지_않는다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.04", "14.0"),
            measurement(2, date(2026, 9, 1), "100.16", "15.0"),
        ]
        assert "키 5.12cm" in body(rows)


class TestDirection:
    """요약은 방향을 동사로 말한다 — 늘었어요 / 줄었어요 / 그대로예요 (G-9, #282 리뷰).

    "키 -0.3cm 늘었어요" 가 되지 않게 숫자는 크기만 적는다. 두 지표가 같은 쪽이면 동사 하나로
    묶는다.
    """

    PERIOD = "2026년 3월 1일부터 2026년 9월 1일까지 6개월간"

    @pytest.mark.parametrize(
        ("before", "after", "summary"),
        [
            (("95.0", "14.0"), ("97.1", "14.5"), "키 2.1cm · 몸무게 0.5kg 늘었어요."),
            (("95.5", "14.5"), ("95.2", "14.2"), "키 0.3cm · 몸무게 0.3kg 줄었어요."),
            (("95.0", "14.0"), ("97.1", "13.8"), "키 2.1cm 늘고 몸무게 0.2kg 줄었어요."),
            (("95.5", "14.0"), ("95.2", "14.5"), "키 0.3cm 줄고 몸무게 0.5kg 늘었어요."),
            (("95.0", "14.0"), ("97.0", "14.0"), "키 2.0cm 늘고 몸무게는 그대로예요."),
            (("95.0", "14.0"), ("95.0", "14.6"), "키는 그대로이고 몸무게 0.6kg 늘었어요."),
            (("95.0", "14.0"), ("95.0", "14.0"), "키와 몸무게 모두 그대로예요."),
        ],
    )
    def test_두_지표의_방향을_동사로_말한다(self, before, after, summary):
        rows = [
            measurement(1, date(2026, 3, 1), *before),
            measurement(2, date(2026, 9, 1), *after),
        ]
        assert body(rows).split("\n")[0] == f"{self.PERIOD} {summary}"

    @pytest.mark.parametrize(
        ("after", "summary"),
        [
            ("105.0", "키 10.0cm 늘었어요."),
            ("94.7", "키 0.3cm 줄었어요."),
            ("95.0", "키는 그대로예요."),
        ],
    )
    def test_한_지표도_방향을_동사로_말한다(self, after, summary):
        rows = [
            measurement(1, date(2026, 3, 1), "95.0", None),
            measurement(2, date(2026, 9, 1), after, None),
        ]
        assert body(rows).split("\n")[0] == f"{self.PERIOD} {summary}"

    def test_측정일이_다르면_줄마다_방향을_말한다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "100.0", "15.0"),
            measurement(2, date(2026, 5, 1), None, "14.7"),
            measurement(3, date(2026, 8, 1), "101.2", None),
        ]
        assert body(rows).split("\n")[:2] == [
            "2026년 3월 1일부터 2026년 8월 1일까지 5개월간 키 1.2cm 늘고,",
            "2026년 3월 1일부터 2026년 5월 1일까지 2개월간 몸무게 0.3kg 줄었어요.",
        ]

    def test_요약에는_빼기_부호가_없고_직전_대비는_부호_그대로다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.5", "14.5"),
            measurement(2, date(2026, 9, 1), "95.2", "14.5"),
        ]
        summary, _, latest = body(rows).split("\n")
        assert "-" not in summary
        assert latest.endswith("(직전 대비 키 -0.3cm · 몸무게 0.0kg)")

    def test_줄었다는_말도_평가가_아니다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.5", "14.5"),
            measurement(2, date(2026, 9, 1), "95.2", "14.2"),
        ]
        assert find_evaluative(body(rows)) == ()


class TestOneSide:
    def rows(self):
        return [
            measurement(1, date(2026, 3, 1), "100.0", "15.0"),  # 둘 다
            measurement(2, date(2026, 5, 1), None, "15.8"),  # 몸무게만
            measurement(3, date(2026, 8, 1), "101.2", None),  # 키만
        ]

    def test_지표별로_요약하고_줄마다_그날_잰_값만_적는다(self):
        assert body(self.rows()) == (
            "2026년 3월 1일부터 2026년 8월 1일까지 5개월간 키 1.2cm,\n"
            "2026년 3월 1일부터 2026년 5월 1일까지 2개월간 몸무게 0.8kg 늘었어요.\n"
            "  2026년 3월 1일  키 100.0cm · 몸무게 15.0kg\n"
            "  2026년 5월 1일  몸무게 15.8kg  (직전 대비 몸무게 0.8kg)\n"
            "  2026년 8월 1일  키 101.2cm  (직전 대비 키 1.2cm)"
        )

    def test_직전_대비는_그_지표를_잰_바로_앞_측정과_비교한다(self):
        """8월 1일 키는 몸무게만 잰 5월 1일이 아니라 3월 1일 키와 비교한다."""
        text = body(self.rows())
        assert "키 101.2cm  (직전 대비 키 1.2cm)" in text

    def test_빈_칸을_0이나_대시로_채우지_않는다(self):
        text = body(self.rows())
        assert "–" not in text and "-" not in text
        assert "키 0" not in text.split("\n")[3]  # 몸무게만 잰 줄에 키가 없다

    def test_한_지표만_2건_이상이면_그_지표만_요약한다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.0", "14.0"),
            measurement(2, date(2026, 9, 1), "105.0", None),
        ]
        assert body(rows) == (
            "2026년 3월 1일부터 2026년 9월 1일까지 6개월간 키 10.0cm 늘었어요.\n"
            "  2026년 3월 1일  키 95.0cm · 몸무게 14.0kg\n"
            "  2026년 9월 1일  키 105.0cm  (직전 대비 키 10.0cm)\n" + ONE_METRIC_WEIGHT
        )

    def test_한_지표만_2건이어도_need_more_로_막지_않는다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.0", None),
            measurement(2, date(2026, 9, 1), "105.0", None),
        ]
        text = body(rows)
        assert NEED_MORE not in text
        assert text.endswith(ONE_METRIC_WEIGHT)
        assert "몸무게" in text and "kg" not in text.replace(ONE_METRIC_WEIGHT, "")

    def test_몸무게만_2건이면_키_안내가_붙는다(self):
        rows = [
            measurement(1, date(2026, 3, 1), None, "14.0"),
            measurement(2, date(2026, 9, 1), None, "17.5"),
        ]
        assert body(rows).endswith(ONE_METRIC_HEIGHT)


class TestNeedMore:
    def test_측정_1건은_숫자를_만들지_않고_안내만_나간다(self):
        assert body([measurement(1, date(2026, 3, 1), "95.0", "14.0")]) == NEED_MORE

    def test_측정이_없어도_안내다(self):
        assert body([]) == NEED_MORE

    def test_두_지표가_각각_1건이면_안내다(self):
        rows = [
            measurement(1, date(2026, 3, 1), "95.0", None),
            measurement(2, date(2026, 9, 1), None, "17.5"),
        ]
        assert body(rows) == NEED_MORE


class TestPosture:
    def heights(self, *month_days):
        return [
            measurement(i, day, f"{90 + i}.0", None) for i, day in enumerate(month_days, start=1)
        ]

    def test_24개월_전후를_걸치면_자세_단서가_붙는다(self):
        birth = date(2024, 1, 10)
        rows = self.heights(
            date(2025, 9, 10), date(2025, 12, 10), date(2026, 3, 10)
        )  # 20 · 23 · 26
        text = body(rows, birth=birth)
        assert POSTURE in text.split("\n")
        assert "키 2.0cm" in text  # 숫자는 고치지 않는다

    def test_안심_문장은_넣지_않는다(self):
        rows = self.heights(date(2025, 9, 10), date(2026, 3, 10))
        text = body(rows, birth=date(2024, 1, 10))
        assert "괜찮" not in text
        assert find_evaluative(text) == ()

    def test_24개월_전만이면_붙지_않는다(self):
        rows = self.heights(date(2025, 9, 10), date(2025, 12, 10))  # 20 · 23
        assert POSTURE not in body(rows, birth=date(2024, 1, 10))

    def test_24개월_후만이면_붙지_않는다(self):
        rows = self.heights(date(2026, 3, 10), date(2026, 6, 10))  # 26 · 29
        assert POSTURE not in body(rows, birth=date(2024, 1, 10))

    def test_정확히_24개월인_날은_후로_센다(self):
        rows = self.heights(date(2025, 12, 10), date(2026, 1, 10))  # 23 · 24
        assert POSTURE in body(rows, birth=date(2024, 1, 10))

    def test_생일_전_날짜는_월령을_못_세서_건너뛴다(self):
        rows = self.heights(date(2023, 12, 1), date(2026, 3, 10))
        text = body(rows, birth=date(2024, 1, 10))  # 예외 없이 돈다
        assert POSTURE not in text

    def test_몸무게만_걸쳐도_붙지_않는다(self):
        rows = [
            measurement(1, date(2025, 9, 10), None, "11.0"),
            measurement(2, date(2026, 3, 10), None, "13.0"),
        ]
        assert POSTURE not in body(rows, birth=date(2024, 1, 10))


class TestJudgement:
    ROWS = [
        measurement(1, date(2026, 3, 12), "95.0", "14.0"),
        measurement(2, date(2026, 9, 12), "105.0", "17.5"),
    ]

    def test_판정을_물어도_같은_추이에_검진_안내만_더한다(self):
        plain = body(self.ROWS)
        asked = body(self.ROWS, judgement=True)
        assert asked == plain + "\n" + CHECKUP

    def test_측정이_모자라도_검진_안내는_붙는다(self):
        text = body([measurement(1, date(2026, 3, 1), "95.0", "14.0")], judgement=True)
        assert text == NEED_MORE + "\n" + CHECKUP

    def test_판정어를_만들지_않는다(self):
        for judgement in (False, True):
            assert find_evaluative(body(self.ROWS, judgement=judgement)) == ()
        for banned in ("잘 크", "정상", "또래", "작은 편", "빠른", "늦"):
            assert banned not in body(self.ROWS, judgement=True)

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("T24", False),  # 6개월 동안 얼마나 컸어?
            ("T25", False),  # 올해 몇 cm 컸어?
            ("T26", False),  # 몸무게는 얼마나 늘었어?
            ("T27", True),  # 잘 크고 있어?
            ("T28", True),  # 10cm 컸는데 많이 큰 거야?
            ("T29", True),  # 또래보다 작은 편이야?
        ],
    )
    def test_판정을_묻는_질문을_가린다(self, key, expected):
        assert asks_judgement([doc_inputs()[key]]) is expected

    def test_여러_문장_중_하나라도_판정을_물으면_참이다(self):
        assert asks_judgement(["올해 몇 cm 컸어?", "잘 크고 있어?"]) is True
        assert asks_judgement([]) is False


class TestSameDay:
    def test_같은_날_두_번_잰_기록은_입력_순서대로_따로_적는다(self):
        rows = [
            measurement(1, date(2026, 9, 1), "104.0", None),
            measurement(2, date(2026, 9, 1), "104.4", None),
        ]
        text = body(rows)
        lines = text.split("\n")
        assert lines[1].endswith("키 104.0cm")
        assert lines[2].endswith("키 104.4cm  (직전 대비 키 0.4cm)")
        assert "0일간" in lines[0]
