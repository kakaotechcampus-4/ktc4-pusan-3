"""복약 시점 사전 · 기간 · 원문 대조 — 모델은 표현을 복사만 하고 anchor 는 코드가 정한다.

- "하루 세 번 식후" → 08:30 · 12:30 · 17:30. 끼니도 시각도 없는 "하루 세 번" 은 풀지 않는다
- 시각은 짐작하지 않는다 — 오전 · 오후를 모르는 "3시" 는 풀지 않는다
- 영아(0–23개월)는 자기 전을 취침 상수 대신 19:30 으로 낸다 (M-3)
- offset 은 DB CHECK(-120 ~ 180) 안이다. 밖이면 자르지 않고 풀지 않는다
- 기간은 오늘 포함이다. 기간이 없으면 ends_on 을 짐작하지 않는다
- span 은 원문의 부분 문자열(공백 정규화 후)이다
"""

from datetime import time, timedelta

import pytest

from app.agents.health.tools.spans import is_verbatim
from app.agents.health.tools.timing import (
    INFANT_BEDTIME,
    anchors_named,
    resolve_dose_answer,
    resolve_dose_timing,
    resolve_period,
    unclear_clock,
)
from app.core.medication_draft import OFFSET_MAX, OFFSET_MIN
from tests.unit.agents.health.support import TODAY


def resolved(spans, months=48):
    result = resolve_dose_timing(spans, months=months)
    assert result is not None, f"{spans} 가 풀리지 않았다"
    return result


def times(spans, months=48):
    return [d.scheduled_time for d in resolved(spans, months)]


class TestMealTiming:
    def test_하루_세_번_식후는_세_끼니_30분_뒤(self):
        doses = resolved(["하루 세 번 식후"])

        assert [(d.timing_anchor, d.offset_minutes) for d in doses] == [
            ("breakfast", 30),
            ("lunch", 30),
            ("dinner", 30),
        ]
        assert [d.scheduled_time for d in doses] == [time(8, 30), time(12, 30), time(17, 30)]

    def test_하루_두_번_식후는_아침_저녁(self):
        assert times(["하루 두 번 식후"]) == [time(8, 30), time(17, 30)]

    def test_끼니가_붙으면_그_끼니만(self):
        assert times(["점심 저녁 식후"]) == [time(12, 30), time(17, 30)]

    def test_아침_먹기_30분_전(self):
        [dose] = resolved(["아침 먹기 30분 전"])

        assert (dose.timing_anchor, dose.offset_minutes) == ("breakfast", -30)
        assert dose.scheduled_time == time(7, 30)

    def test_저녁_먹기_30분_전은_16시_30분(self):
        assert times(["저녁 먹기 30분 전"]) == [time(16, 30)]

    @pytest.mark.parametrize(
        ("span", "offset"),
        [
            ("하루 세 번 식후", 30),
            ("하루 세 번 밥 먹고", 30),
            ("하루 세 번 식후 30분", 30),
            ("하루 세 번 식후 바로", 0),
            ("하루 세 번 먹고 바로", 0),
            ("하루 세 번 식전", -30),
            ("하루 세 번 밥 먹기 전", -30),
            ("하루 세 번 식전 30분", -30),
            ("하루 세 번 식후 1시간", 60),
        ],
    )
    def test_표현_사전(self, span, offset):
        assert {d.offset_minutes for d in resolved([span])} == {offset}

    def test_끼니마다_시점이_다를_수_있다(self):
        assert times(["아침 식후", "저녁 식전"]) == [time(8, 30), time(16, 30)]

    def test_되묻기_답과_합치면_풀린다(self):
        # "하루 세 번" 만으로는 못 풀어 되물었고, "식후요" 로 답했다
        assert resolve_dose_timing(["하루 세 번"], months=48) is None

        assert times(["하루 세 번", "식후요"]) == [time(8, 30), time(12, 30), time(17, 30)]

    def test_점심_먹고_바로(self):
        assert times(["점심 먹고 바로"]) == [time(12, 0)]


class TestUnresolved:
    @pytest.mark.parametrize(
        "spans",
        [
            ["하루 세 번"],  # 끼니도 시각도 없다 — 식후로 채우지 않는다
            ["식후"],  # 어느 끼니인지 모른다
            ["하루 한 번"],
            [],
            [""],
            ["하루 네 번 식후"],  # 끼니로 나눌 수 없다
        ],
    )
    def test_시점이_없으면_해결하지_않는다(self, spans):
        assert resolve_dose_timing(spans, months=48) is None

    def test_오전_오후를_모르는_시각은_짐작하지_않는다(self):
        assert resolve_dose_timing(["3시에"], months=48) is None

    def test_개수가_안_맞으면_해결하지_않는다(self):
        assert resolve_dose_timing(["하루 세 번 오후 3시"], months=48) is None

    @pytest.mark.parametrize("span", ["하루 세 번 식후 5시간", "하루 세 번 식전 3시간"])
    def test_offset_범위_밖은_자르지_않고_풀지_않는다(self, span):
        assert resolve_dose_timing([span], months=48) is None

    def test_offset_범위는_DB_CHECK_안(self):
        for span in ("하루 세 번 식후 2시간", "하루 세 번 식전 2시간", "하루 세 번 식후 3시간"):
            for dose in resolved([span]):
                assert OFFSET_MIN <= dose.offset_minutes <= OFFSET_MAX


class TestClockTime:
    def test_오후_3시는_fixed(self):
        [dose] = resolved(["오후 3시"])

        assert (dose.timing_anchor, dose.offset_minutes, dose.fixed_time) == (
            "fixed",
            0,
            time(15, 0),
        )
        assert dose.scheduled_time == time(15, 0)

    @pytest.mark.parametrize(
        ("span", "expected"),
        [
            ("15시", time(15, 0)),
            ("저녁 7시", time(19, 0)),
            ("오전 9시 반", time(9, 30)),
            ("오후 3시 20분", time(15, 20)),
            ("아침 8시", time(8, 0)),
            ("밤 9시", time(21, 0)),
            ("새벽 3시", time(3, 0)),
            ("저녁 시간 7시로", time(19, 0)),  # "저녁 시간 7시로 바꿔줘" 에서 복사한 말
        ],
    )
    def test_N시(self, span, expected):
        assert times([span]) == [expected]

    def test_시각이_여럿이면_시각순(self):
        assert times(["오후 3시", "오전 9시"]) == [time(9, 0), time(15, 0)]


class TestSpecialAnchors:
    def test_일어나서는_wake_up(self):
        [dose] = resolved(["일어나서"])

        assert (dose.timing_anchor, dose.offset_minutes) == ("wake_up", 0)
        assert dose.scheduled_time == time(7, 30)

    def test_24개월부터는_자기_전이_20시_30분(self):
        [dose] = resolved(["자기 전"], months=24)

        assert (dose.timing_anchor, dose.offset_minutes, dose.fixed_time) == ("bedtime", 0, None)
        assert dose.scheduled_time == time(20, 30)

    def test_영아는_자기_전이_19시_30분(self):
        [dose] = resolved(["자기 전"], months=23)

        assert (dose.timing_anchor, dose.fixed_time) == ("fixed", INFANT_BEDTIME)
        assert dose.scheduled_time == time(19, 30)

    def test_0개월도_영아(self):
        assert times(["자기 전에"], months=0) == [time(19, 30)]


class TestAnchorsNamed:
    """수정 초안이 어느 회차를 바꾸는지 가린다 — 말에 끼니가 있으면 그 회차만 바뀐다."""

    @pytest.mark.parametrize(
        ("spans", "expected"),
        [
            (["저녁 시간 7시"], {"dinner"}),
            (["아침 점심 식후"], {"breakfast", "lunch"}),
            (["자기 전"], {"bedtime"}),
            (["일어나서"], {"wake_up"}),
            (["하루 세 번 식후"], set()),
            (["오후 3시"], set()),
        ],
    )
    def test_말에_들어_있는_기준점(self, spans, expected):
        assert anchors_named(spans) == expected


class TestPeriod:
    def test_5일은_오늘_포함_5일(self):
        assert resolve_period("5일", today=TODAY) == (TODAY, TODAY + timedelta(days=4))

    def test_기간이_없으면_ends_on_은_비운다(self):
        assert resolve_period(None, today=TODAY) == (TODAY, None)

    @pytest.mark.parametrize(
        ("span", "days"),
        [("5일 먹이래", 5), ("일주일", 7), ("2주", 14), ("열흘", 10), ("이틀", 2), ("사흘", 3)],
    )
    def test_기간_표현(self, span, days):
        assert resolve_period(span, today=TODAY) == (TODAY, TODAY + timedelta(days=days - 1))

    @pytest.mark.parametrize("span", ["다음 주 월요일까지", "0일", "언젠가까지"])
    def test_못_읽으면_None(self, span):
        assert resolve_period(span, today=TODAY) is None


class TestVerbatim:
    def test_원문에_없는_이름은_거절(self):
        assert is_verbatim("아목시실린", ["항생제 하루 세 번 식후에 5ml씩 먹여야 해."]) is False

    def test_원문의_부분_문자열은_통과(self):
        assert is_verbatim("5ml", ["항생제 하루 세 번 식후에 5ml씩 먹여야 해."]) is True

    def test_공백은_정규화해서_본다(self):
        assert is_verbatim("항생제  시럽", ["항생제 시럽 먹여야 해"]) is True
        assert is_verbatim("항생제 시럽", ["항생제\n시럽 먹여야 해"]) is True

    def test_빈_span_은_거절(self):
        assert is_verbatim("  ", ["아무 말"]) is False

    def test_여러_원문_중_하나에_있으면_통과(self):
        assert is_verbatim("시럽", ["항생제 먹여야 해", "시럽도 있어"]) is True


# ── 최종 리뷰 반영 — 틀린 값을 내는 입력은 풀지 않는다 ──
class TestVerbatimBoundary:
    """숫자 · 소수점 · 영문 한가운데에서 자른 말은 원문에 있는 값이 아니다."""

    @pytest.mark.parametrize(
        ("span", "source"),
        [
            ("5ml", "0.5ml 하루 세 번"),
            ("5ml", "2.5ml 하루 세 번"),
            ("5ml", "15ml 하루 세 번"),
            ("5", "5ml 하루 세 번"),
            ("5ml", "5ml/kg 으로"),
            ("5일", "15일 먹이래"),
            ("항생제", "항생제A 하루 세 번"),
        ],
    )
    def test_경계_한가운데에서_자른_말은_거절(self, span, source):
        assert is_verbatim(span, [source]) is False

    @pytest.mark.parametrize(
        ("span", "source"),
        [
            ("5ml", "항생제 5ml씩 먹여야 해"),
            ("5ml", "식후에 5ml."),
            ("5ml", "5ml, 하루 세 번"),
            ("5ml", "(5ml)"),
            ("5일", "5일 먹이래"),
            ("5ml", "0.5ml 말고 5ml 로"),
        ],
    )
    def test_경계가_맞으면_통과(self, span, source):
        assert is_verbatim(span, [source]) is True


class TestWrongTimeIsNone:
    @pytest.mark.parametrize(
        "spans",
        [
            ["아침 8시, 그리고 2시"],  # 두 번째 시각에 아침이 붙어 새벽 2시가 된다
            ["아침 약 8시 반으로, 저녁은 7시"],  # 표지 둘 · 시각 둘 — 어느 것이 어느 것인지 모른다
            ["점심은 빼고 저녁만 7시로"],  # 빼라는 점심이 끼니로 남는다
            ["아침 먹고, 7시"],  # 아침 식후가 사라진다
            ["저녁 일곱 시"],  # 한글 숫자 시각은 조용히 버려진다
            ["밤 5시"],  # 새벽인지 저녁인지 모른다
        ],
    )
    def test_짐작으로_시각을_만들지_않는다(self, spans):
        assert resolve_dose_timing(spans, months=48) is None

    @pytest.mark.parametrize(
        ("span", "expected"),
        [
            ("밤 1시", time(1, 0)),
            ("밤 2시에", time(2, 0)),
            ("밤 12시", time(0, 0)),
            ("밤 11시", time(23, 0)),
        ],
    )
    def test_밤은_새벽_시각도_있다(self, span, expected):
        assert times([span]) == [expected]

    @pytest.mark.parametrize("span", ["저녁은 7시", "저녁에는 7시", "아침은 8시 반"])
    def test_끼니_뒤_조사는_표지로_본다(self, span):
        assert len(resolved([span])) == 1

    def test_표지가_하나_시각이_하나일_때만_떨어진_표지를_쓴다(self):
        assert times(["저녁 시간 7시로"]) == [time(19, 0)]
        assert resolve_dose_timing(["저녁 시간 7시로 아침 시간 8시로"], months=48) is None

    @pytest.mark.parametrize("answer", ["저녁이요", "저녁 8시요", "저녁 8시"])
    def test_애매한_시각은_오전_오후로_답하면_푼다(self, answer):
        # 원래 말("8시에")만으로도, 이어 붙여서도 모호하다 — 답이 오전 · 오후를 말해야 풀린다
        assert resolve_dose_timing(["8시에"], months=48) is None
        [dose] = resolve_dose_answer(["8시에"], answer, months=48) or []
        assert dose.scheduled_time == time(20, 0)

    def test_답이_오전_오후가_아니면_계속_모른다(self):
        assert resolve_dose_answer(["8시에"], "식후요", months=48) is None
        assert resolve_dose_answer(["8시에"], "몰라", months=48) is None

    def test_모호하지_않았던_표현은_답을_그대로_이어_붙인다(self):
        got = resolve_dose_answer(["하루 세 번"], "식후요", months=48)

        assert [d.scheduled_time for d in got or []] == [time(8, 30), time(12, 30), time(17, 30)]

    def test_시각이_애매한지_안다(self):
        assert unclear_clock(["8시에"]) is True
        assert unclear_clock(["저녁 일곱 시"]) is True
        assert unclear_clock(["하루 세 번"]) is False
        assert unclear_clock(["오후 3시"]) is False


class TestPeriodIsDurationOnly:
    @pytest.mark.parametrize(
        "span",
        ["10월 25일까지", "25일까지", "5일째", "3일에서 5일", "25일부터", "다음 달 3일"],
    )
    def test_날짜는_기간으로_읽지_않는다(self, span):
        assert resolve_period(span, today=TODAY) is None

    @pytest.mark.parametrize(
        "span", ["5일", "5일 먹이래", "5일간", "5일치", "5일 동안", "7일 정도"]
    )
    def test_기간_모양은_읽는다(self, span):
        assert resolve_period(span, today=TODAY)[1] == TODAY + timedelta(days=int(span[0]) - 1)
