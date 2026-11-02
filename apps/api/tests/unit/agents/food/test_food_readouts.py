"""코드 상수 문구가 Food_Tool_명세.md §4 와 글자 단위로 같은지.

문구는 모델을 거치지 않고 화면에 그대로 나간다. 명세와 코드가 따로 놀지 않게 표를 읽어 비교한다.
"""

import re
from pathlib import Path

import pytest

from app.agents.food import readouts
from app.agents.food.readouts import READOUTS

_REPO_ROOT = Path(__file__).resolve().parents[6]
_FOOD_DOCS = _REPO_ROOT / "docs" / "agents" / "food"

_ROW = re.compile(r"^\| `([a-z_.0-9]+)` \| (.+) \|$", re.M)


def spec_rows() -> dict[str, str]:
    text = (_FOOD_DOCS / "Food_Tool_명세.md").read_text(encoding="utf-8")
    section = text.split("## 4. 출력 상수")[1]
    return dict(_ROW.findall(section))


def test_명세_4절_표와_문구가_같다() -> None:
    assert spec_rows() == {key: READOUTS.get(key).template for key in spec_rows()}


def test_값이_들어가는_문구는_자리를_채운다() -> None:
    assert READOUTS.render("nutrient.insufficient", recorded=8).body.endswith("(8건 기록됨)")


class TestDocParity:
    def test_명세_표의_키와_코드_키가_같다(self) -> None:
        assert set(spec_rows()) == set(READOUTS.texts)

    @pytest.mark.parametrize("key", sorted(spec_rows()))
    def test_문구가_글자_단위로_같다(self, key: str) -> None:
        assert spec_rows()[key] == READOUTS.get(key).template


class TestCatalog:
    def test_코드_상수는_전부_yaml_에_있다(self) -> None:
        keys = {
            value
            for name, value in vars(readouts).items()
            if name.isupper() and isinstance(value, str) and name != "READOUTS"
        }
        assert keys == set(READOUTS.texts)

    def test_렌더링한_readout_은_코드가_쓴_것이다(self) -> None:
        for key, text in READOUTS.texts.items():
            values = {name: "값" for name in re.findall(r"{(\w+)}", text.template)}
            readout = READOUTS.render(key, **values)
            assert readout.authored_by == "code"
            assert readout.body.strip()

    def test_모르는_키는_바로_실패한다(self) -> None:
        with pytest.raises(KeyError):
            READOUTS.get("closed.없는키")


BANNED = ("먹여도 돼", "먹어도 돼", "괜찮", "안 돼", "드셔도")


def test_섭취_확인과_요청_안내_문구에는_허가_표현이_없다() -> None:
    keys = [key for key in READOUTS.texts if key.startswith(("check.", "meal.request_"))]
    assert keys
    for key in keys:
        assert not any(word in READOUTS.get(key).template for word in BANNED), key
