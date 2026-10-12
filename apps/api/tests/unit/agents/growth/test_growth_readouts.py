"""코드 상수 문구가 Growth_Tool_명세.md §4 와 글자 단위로 같은지.

문구는 모델을 거치지 않고 화면에 그대로 나간다. 명세와 코드가 따로 놀지 않게 표를 읽어 비교한다.
명세 셀 끝의 " (Activity 와 같은 문구)" 같은 괄호 주석만 문구가 아니다.
"""

import re

import pytest

from app.agents.growth import readouts
from app.agents.growth.readouts import READOUTS
from tests.unit.agents.growth.support import GROWTH_DOCS

_ROW = re.compile(r"^\| `([a-z_.0-9]+)` \| (.+) \|$", re.M)
_NOTE = re.compile(r"^ \(.+\)$")


def doc_constants() -> dict[str, str]:
    text = (GROWTH_DOCS / "Growth_Tool_명세.md").read_text(encoding="utf-8")
    section = text.split("## 4. 출력 상수")[1]
    return dict(_ROW.findall(section))


class TestDocParity:
    def test_명세_표의_키와_코드_키가_같다(self):
        assert set(doc_constants()) == set(READOUTS.texts)

    @pytest.mark.parametrize("key", sorted(doc_constants()))
    def test_문구가_글자_단위로_같다(self, key):
        cell = doc_constants()[key]
        template = READOUTS.get(key).template
        assert cell.startswith(template), f"{key}: 명세 {cell!r} ≠ 코드 {template!r}"
        rest = cell[len(template) :]
        assert rest == "" or _NOTE.match(rest), f"{key}: 문구 뒤에 괄호 주석 말고 다른 글자가 있다"


class TestCatalog:
    def test_코드_상수는_전부_yaml_에_있다(self):
        keys = {
            value
            for name, value in vars(readouts).items()
            if name.isupper() and isinstance(value, str) and name != "READOUTS"
        }
        assert keys == set(READOUTS.texts)

    def test_렌더링한_readout_은_코드가_쓴_것이다(self):
        for key, text in READOUTS.texts.items():
            values = {name: "값" for name in re.findall(r"{(\w+)}", text.template)}
            readout = READOUTS.render(key, **values)
            assert readout.authored_by == "code"
            assert readout.body.strip()

    def test_자리표시자는_채울_값만_받는다(self):
        assert READOUTS.render("delta.one_metric", metric="몸무게").body == (
            "몸무게는 두 번 이상 재면 변화도 함께 알려드릴게요."
        )
        assert READOUTS.render("ask.routine_current", subject="양치하기").body == (
            "양치하기은(는) 요즘 어느 정도까지 혼자 하나요?"
        )
        assert READOUTS.render("caution.non_food_allergy", label="꽃가루").body == (
            "등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요."
        )

    def test_모르는_키는_바로_실패한다(self):
        with pytest.raises(KeyError):
            READOUTS.get("closed.없는키")
