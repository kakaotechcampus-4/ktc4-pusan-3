"""코드 상수 문구가 Health_Tool_명세.md §4 와 글자 단위로 같은지.

문구는 모델을 거치지 않고 화면에 그대로 나간다. 명세와 코드가 따로 놀지 않게 표를 읽어 비교한다.
명세 §4 에는 문구 뒤의 괄호 주석이 없다. "({ends_on})" 처럼 괄호로 끝나는 문구가 있어서 주석을
허용하면 잘린 문구가 통과한다 — 그래서 셀 전체가 글자 그대로 같아야 한다.
"""

import re

import pytest

from app.agents.health import readouts
from app.agents.health.readouts import READOUTS
from tests.unit.agents.health.support import HEALTH_DOCS

_ROW = re.compile(r"^\| `([a-z_.0-9]+)` \| (.+) \|$", re.M)

# 명세 §8 채널 표의 종류
KINDS = {
    "symptom_repeat",
    "checkup_schedule",
    "checkup_context",
    "medication_notice",
    "dose_status",
    "ask",
    "fever_timeline",
    "emergency",
    "med_notice_form",
    "emergency_card",
    "prescription_draft",
    "blocked",
    "symptom_timeline",
    "place_list",
    "caution",
    "notice",
    "unsupported",
}


def doc_constants() -> dict[str, str]:
    text = (HEALTH_DOCS / "Health_Tool_명세.md").read_text(encoding="utf-8")
    section = text.split("## 4. 출력 상수")[1]
    return dict(_ROW.findall(section))


def test_명세의_문구는_73개다():
    assert len(doc_constants()) == 73


class TestDocParity:
    def test_명세_표의_키와_코드_키가_같다(self):
        assert set(doc_constants()) == set(READOUTS.texts)

    @pytest.mark.parametrize("key", sorted(doc_constants()))
    def test_문구가_글자_단위로_같다(self, key):
        cell = doc_constants()[key]
        template = READOUTS.get(key).template
        assert cell.startswith(template), f"{key}: 명세 {cell!r} ≠ 코드 {template!r}"
        rest = cell[len(template) :]
        assert rest == "", f"{key}: 명세 문구가 코드보다 길다 — 끝이 잘렸다: {rest!r}"


class TestCatalog:
    def test_상수와_yaml_키가_같다(self):
        keys = {
            value
            for name, value in vars(readouts).items()
            if name.isupper() and isinstance(value, str) and name != "READOUTS"
        }
        assert keys == set(READOUTS.texts)

    def test_전부_코드가_쓴_문구다(self):
        for key, text in READOUTS.texts.items():
            values = {name: "값" for name in re.findall(r"{(\w+)}", text.template)}
            readout = READOUTS.render(key, **values)
            assert readout.authored_by == "code"
            assert readout.body.strip()

    def test_kind_는_정해진_값이다(self):
        assert {READOUTS.get(k).kind for k in READOUTS.texts} <= KINDS

    def test_없는_키는_KeyError(self):
        with pytest.raises(KeyError):
            READOUTS.get("closed.없는키")
