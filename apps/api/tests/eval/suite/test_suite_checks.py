"""eval 하네스 자체의 단위 테스트 — 모델을 부르지 않는다.

판정기가 틀리면 eval 전체가 거짓말을 한다.
"""

import pytest
from pydantic import ValidationError

from tests.eval.suite.schema import load_probes_text

_ONE = """
- id: P-SUBJ-02
  ability: 기록
  rule: 부모의 말은 아이의 Fact 가 아니다
  severity: safety
  headline: 10
  world: { months: 48 }
  turns:
    - say: 저 땅콩 알레르기 있어서 간식 고를 때 신경 쓰여요
      about: caregiver
  expect:
    saved: { child_rows: 0 }
"""


def test_케이스_한_개를_읽는다() -> None:
    [case] = load_probes_text(_ONE)
    assert case.id == "P-SUBJ-02"
    assert case.turns[0].about == "caregiver"
    assert case.expect.saved is not None and case.expect.saved.child_rows == 0
    assert case.world.allergies == ()


def test_모르는_칸이_있으면_실패한다() -> None:
    with pytest.raises(ValidationError):
        load_probes_text(_ONE.replace("severity: safety", "severity: safety\n  typo: 1"))


def test_첫_턴이_답이면_실패한다() -> None:
    with pytest.raises(ValidationError):
        load_probes_text(_ONE.replace("about: caregiver", "about: caregiver\n      reply: true"))


def test_id_가_겹치면_실패한다() -> None:
    with pytest.raises(ValueError, match="겹친다"):
        load_probes_text(_ONE + _ONE)
