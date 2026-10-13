"""Agent 가 쓰는 월령 · 단계 계산은 `age_gates.yaml` 의 경계를 쓴다 (#372).

`rules/age.py` 는 경계를 인자로 받는 순수 함수이고, 여기 함수가 파일에서 읽은 경계를 넘긴다.
"""

from dataclasses import replace
from datetime import date

import pytest

from app.agents.common import age
from app.agents.common.reference import age_gates


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
def test_stage_of_는_파일의_경계로_가른다(months, stage):
    assert age.stage_of(months) == stage


def test_life_stage_는_월령과_단계와_밴드를_준다():
    result = age.life_stage(date(2025, 9, 22), date(2026, 9, 22))
    assert (result.months, result.stage, result.big) == (12, "toddler", "toddler")


@pytest.mark.parametrize(
    ("stage", "months"),
    [("infant_milk", 0), ("infant_weaning", 4), ("toddler", 12), ("preschool", 36)],
)
def test_first_month_of_는_단계가_시작하는_월령이다(stage, months):
    assert age.first_month_of(stage) == months


def test_파일의_경계가_바뀌면_단계도_바뀐다(monkeypatch):
    """함수 안에 월령 숫자가 남아 있으면 파일을 바꿔도 단계가 그대로다."""
    shifted = replace(
        age_gates(),
        stage_boundaries=(
            (0, "infant_milk"),
            (6, "infant_weaning"),
            (18, "toddler"),
            (48, "preschool"),
        ),
    )
    monkeypatch.setattr(age, "age_gates", lambda: shifted)

    assert age.stage_of(5) == "infant_milk"
    assert age.stage_of(17) == "infant_weaning"
    assert age.first_month_of("toddler") == 18
    assert age.life_stage(date(2025, 3, 22), date(2026, 3, 22)).stage == "infant_weaning"
