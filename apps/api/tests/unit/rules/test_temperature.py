"""발화에 적힌 체온 숫자 읽기 — 단위가 붙은 섭씨 숫자만 센다.

단위 없는 숫자는 날수 · 월령 · 분일 수 있다("생후 38일" 이 38.0 으로 통과하면 안 된다).
"""

from decimal import Decimal

import pytest

from app.rules.temperature import temperatures_said


def test_temperatures_said():
    assert temperatures_said("38도야") == {Decimal("38")}
    assert temperatures_said("38도 5부") == {Decimal("38.5")}
    assert temperatures_said("38.5℃ 나왔어") == {Decimal("38.5")}
    assert temperatures_said("생후 38일") == frozenset()
    assert temperatures_said("100°F") == frozenset()


@pytest.mark.parametrize("text", ["", "열이 나", "38"])
def test_숫자에_단위가_없으면_없다(text):
    assert temperatures_said(text) == frozenset()


def test_여러_번_말한_체온은_모두_센다():
    assert temperatures_said("아침엔 37.8도 저녁엔 38도 5부") == {Decimal("37.8"), Decimal("38.5")}
