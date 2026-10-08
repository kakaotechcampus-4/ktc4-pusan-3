from datetime import datetime

import pytest

from app.api.quota import KST
from app.rules.home_prompts import agent_prompts

WEEKDAY = datetime(2026, 10, 7, tzinfo=KST)  # 수
SATURDAY = datetime(2026, 10, 10, tzinfo=KST)


def _at(day: datetime, hour: int) -> datetime:
    return day.replace(hour=hour)


@pytest.mark.parametrize(
    ("nickname", "shown"),
    [
        ("민준", "민준이가 먹을 저녁 추천해드릴까요?"),  # 받침 있음 → 이가
        ("하나", "하나가 먹을 저녁 추천해드릴까요?"),
        ("Mina", "우리 아이가 먹을 저녁 추천해드릴까요?"),  # 한글이 아니면 받침을 모른다
    ],
)
def test_particles_follow_the_last_syllable(nickname: str, shown: str) -> None:
    [food, _] = agent_prompts(nickname, _at(WEEKDAY, 18))
    assert food.text == shown


def test_activity_uses_the_with_particle() -> None:
    [_, activity] = agent_prompts("민준", _at(WEEKDAY, 18))
    assert activity.text == "민준이랑 할 놀이 추천해드릴까요?"


@pytest.mark.parametrize(
    ("hour", "meal", "plays"),
    [
        (5, "내일 아침", False),  # 밤은 아침 6시 전까지
        (6, "아침", False),  # 주중 아침에는 놀이를 묻지 않는다
        (9, "아침", False),
        (10, "점심", True),
        (13, "점심", True),
        (14, "간식", True),
        (16, "간식", True),
        (17, "저녁", True),
        (19, "저녁", True),
        (20, "내일 아침", False),  # 밤에는 놀이를 묻지 않는다
    ],
)
def test_slot_boundaries_on_a_weekday(hour: int, meal: str, plays: bool) -> None:
    prompts = agent_prompts("하나", _at(WEEKDAY, hour))
    assert prompts[0].text == f"하나가 먹을 {meal} 추천해드릴까요?"
    assert [p.agent for p in prompts] == (["food", "activity"] if plays else ["food"])


def test_weekend_morning_also_asks_about_play() -> None:
    assert [p.agent for p in agent_prompts("하나", _at(SATURDAY, 8))] == ["food", "activity"]
