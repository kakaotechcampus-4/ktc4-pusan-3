"""보호자가 말하지 않은 시간 · 횟수 · 나이 수치를 가려내는 규칙."""

import pytest

from app.rules.amount_grounding import AMOUNT_UNITS, ungrounded_amounts


class TestUngroundedAmounts:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("하루 1시간까지만 보여 주세요", ("1시간",)),
            ("30분만 보여 주기", ("30분",)),
            ("세 번 말해 주기", ("3번",)),
            ("하루 두 번 정해진 시간에", ("2번",)),
            ("5살 아이는 하루 2시간 이내로", ("5살", "2시간")),
            ("반 시간 동안 같이 보기", ("0.5시간",)),
            ("2주 동안 지켜 보기", ("2주",)),
        ],
    )
    def test_보호자가_말하지_않은_시간_수치는_가려낸다(self, text, expected):
        assert ungrounded_amounts([text], []) == expected

    def test_보호자가_말한_수치는_통과한다(self):
        grounds = ["30분만 보여 주는데 끝내기 힘들어해요"]
        assert ungrounded_amounts(["30분 타이머를 맞춰 두기"], grounds) == ()

    def test_한글_숫자와_아라비아_숫자는_같은_수다(self):
        assert ungrounded_amounts(["세 번 말해 주기"], ["3번 말해도 안 들어요"]) == ()
        assert ungrounded_amounts(["3번 말해 주기"], ["세 번 말해도 안 들어요"]) == ()

    def test_단위가_다르면_다른_수치다(self):
        assert ungrounded_amounts(["30분 보기"], ["30초 보기"]) == ("30분",)

    @pytest.mark.parametrize("text", ["1부터 10까지 세기", "블록 3개 쌓기", "한글 공부 놀이"])
    def test_단위가_없는_숫자는_걸지_않는다(self, text):
        assert ungrounded_amounts([text], []) == ()

    @pytest.mark.parametrize(
        "text", ["세트로 모으기", "초등학생 형 따라 하기", "일상에서 찾아보기"]
    )
    def test_낱말_안의_글자는_수치가_아니다(self, text):
        assert ungrounded_amounts([text], []) == ()

    @pytest.mark.parametrize("text", ["블록 1세트 쌓기", "스티커 3초등 놀이", "5일정 짜기"])
    def test_단위_글자로_시작하는_낱말은_수치가_아니다(self, text):
        assert ungrounded_amounts([text], []) == ()

    def test_units_가_없으면_숫자_전부를_본다(self):
        assert ungrounded_amounts(["1부터 10까지 세기"], [], units=None) == ("1", "10")
        assert ungrounded_amounts(["블록 3개"], ["블록 3개"], units=None) == ()

    def test_같은_수치는_한_번만_돌려준다(self):
        assert ungrounded_amounts(["30분 하고 30분 쉬기"], []) == ("30분",)

    def test_여러_칸을_따로_본다(self):
        assert ungrounded_amounts(["30분", "1시간"], ["30분"]) == ("1시간",)

    def test_단위_목록은_시간_횟수_나이를_덮는다(self):
        assert {"분", "시간", "번", "회", "살", "세", "개월"} <= set(AMOUNT_UNITS)
