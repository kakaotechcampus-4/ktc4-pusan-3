"""습관 상황 태그 — 보호자가 말한 trigger 를 코드가 정한 태그로만 옮긴다."""

import pytest

from app.agents.growth.tools.triggers import parse_trigger_terms, trigger_tag_set, trigger_tags


class TestTags:
    @pytest.mark.parametrize(
        ("trigger", "expected"),
        [
            ("긴장할 때", ("anxious",)),
            ("긴장하면", ("anxious",)),
            ("낯선 곳에 가면", ("anxious",)),
            ("화가 나면", ("frustrated",)),
            ("화가 날 때", ("frustrated",)),
            ("혼난 뒤에", ("frustrated",)),
            ("혼났을 때", ("frustrated",)),
            ("졸릴 때", ("tired",)),
            ("잠들기 전에", ("tired",)),
            ("심심할 때", ("bored",)),
        ],
    )
    def test_trigger_를_태그로_옮긴다(self, trigger, expected):
        assert trigger_tags([trigger]) == expected

    def test_어느_태그에도_안_걸리면_비어_있다(self):
        assert trigger_tags(["TV 볼 때"]) == ()

    @pytest.mark.parametrize("trigger", ["동생을 잘 때려요", "자기 전용 컵을 뺏길 때"])
    def test_낱말만_닮은_말은_걸리지_않는다(self, trigger):
        assert trigger_tags([trigger]) == ()

    def test_비었거나_None_이면_비어_있다(self):
        assert trigger_tags([None, "", "  "]) == ()

    def test_여러_관찰의_태그를_겹치지_않게_처음_걸린_순서로_모은다(self):
        assert trigger_tags(["졸릴 때", "긴장할 때", "졸려서"]) == ("tired", "anxious")

    def test_사전의_태그는_영문_소문자_식별자다(self):
        assert trigger_tag_set() == {"anxious", "frustrated", "tired", "bored"}


class TestLoader:
    @staticmethod
    def data(**overrides) -> dict:
        return {"triggers": [{"tag": "anxious", "aliases": ["긴장"], "guards": [], **overrides}]}

    def test_정상_목록은_읽힌다(self):
        (term,) = parse_trigger_terms(self.data())
        assert (term.key, term.aliases) == ("anxious", ("긴장",))

    @pytest.mark.parametrize("data", [{}, {"triggers": []}])
    def test_목록이_없으면_거부한다(self, data):
        with pytest.raises(ValueError, match="triggers"):
            parse_trigger_terms(data)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"tag": "긴장"}, "식별자"),
            ({"tag": "Anxious"}, "식별자"),
            ({"aliases": []}, "aliases"),
            ({"aliases": ["겁"]}, "한 글자"),
            ({"note": "메모"}, "정해지지 않은 칸"),
        ],
    )
    def test_모양이_틀리면_거부한다(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            parse_trigger_terms(self.data(**overrides))

    def test_tag_가_겹치면_거부한다(self):
        data = {
            "triggers": [
                {"tag": "anxious", "aliases": ["긴장"]},
                {"tag": "anxious", "aliases": ["불안"]},
            ]
        }
        with pytest.raises(ValueError, match="겹친다"):
            parse_trigger_terms(data)
