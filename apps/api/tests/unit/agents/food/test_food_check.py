"""Food 가 보호자에게 하는 말 — 섭취 확인 답 · 식단 추천 요청 안내 · 등록 이름 문구.

음식 낱말을 찾고 판정하는 `check_foods` 는 common 이 하고 `agents/common/test_food_words.py` 가
본다. 여기는 그 판정이 문구가 되는 자리만 본다. 모델을 부르지 않는다.
"""

import pytest

from app.agents.common.food_words import check_foods
from app.agents.food.food_check import label_text, request_notices
from app.agents.food.store.ports import SafetyEntry


def entry(label: str, kind: str = "allergy", status: str = "active") -> SafetyEntry:
    return SafetyEntry(kind=kind, label=label, status=status)


NUT = [entry("견과류")]


# ── 음식이 아닌 등록 이름(질환 · 환경)은 음식 낱말로 스캔하지 않는다 ──────────────────────
@pytest.mark.parametrize(
    ("registered", "text"),
    [
        (entry("아토피", kind="chronic_disease"), "아토피인데 우유 먹여도 돼?"),
        (entry("천식", kind="chronic_disease"), "천식이 있는데 우유 먹여도 돼?"),
        (entry("꽃가루", kind="environmental"), "꽃가루가 심한데 간식 먹여도 돼?"),
        (entry("알레르기 비염", kind="other_medical"), "알레르기 비염이 있는데 간식 먹여도 돼?"),
    ],
)
def test_음식이_아닌_등록_이름은_음식_낱말이_아니다(registered, text):
    verdicts = check_foods(text, [registered], stage="preschool", months=50)
    assert not any(v.conflicts for v in verdicts)
    assert registered.label not in [v.word for v in verdicts]
    assert request_notices([text], [registered], stage="preschool", months=50) == ()


# ── /code-review 4 — 빼 달라는 말 · 알레르기를 말하는 말은 요청한 음식이 아니다 ──────────────
@pytest.mark.parametrize(
    ("registered", "text"),
    [
        (NUT, "견과류 빼고 간식 추천해줘"),
        ([entry("땅콩")], "땅콩 안 들어간 간식 추천해줘"),
        ([entry("우유")], "우유 말고 두유로 간식 추천해줘"),
        ([entry("우유")], "우유 대신 먹을 간식 추천해줘"),
        ([entry("땅콩")], "땅콩 없는 간식 추천해줘"),
    ],
)
def test_빼_달라고_한_음식에는_요청_안내가_없다(registered, text):
    assert request_notices([text], registered, stage="preschool", months=50) == ()


# ── /code-review 7 — 문구에 넣는 등록 이름은 보호자가 적은 그대로 ─────────────────────
@pytest.mark.parametrize(
    ("registered", "text"),
    [
        (entry("Egg white"), "Egg white 알레르기"),
        (entry("tree nut"), "tree nut 알레르기"),
        (entry("땅콩 알레르기"), "땅콩 알레르기"),
        (entry("땅콩, 우유 알러지"), "땅콩, 우유 알레르기"),
        (entry("유당불내증", kind="chronic_disease"), "유당불내증"),
    ],
)
def test_등록_이름은_적은_그대로_쓴다(registered, text):
    assert label_text(registered) == text


# ── #575 리뷰 (PM) — 판정을 고치면 요청 안내 · 추천 이유 검사도 같이 고쳐진다 ─────────────
@pytest.mark.parametrize(
    ("registered", "text", "stage", "months", "expected"),
    [
        (
            [entry("우유")],
            "흰우유랑 먹을 간식 추천해줘",
            "preschool",
            50,
            "요청하신 흰우유에는 등록해 두신 알레르기와 겹치는 것이 있어서 그런 메뉴는 빼고 "
            "골랐어요.",
        ),
        (
            [],
            "우유를 넣은 이유식 추천해줘",
            "infant_weaning",
            10,
            "요청하신 우유는 12개월 전에는 먹이지 않아서 빼고 골랐어요.",
        ),
    ],
)
def test_합성어_조사가_붙은_요청에도_안내를_붙인다(registered, text, stage, months, expected):
    notices = request_notices([text], registered, stage=stage, months=months)
    assert [n.body for n in notices] == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "땅콩 빼 주세요 간식 추천해줘",
        "땅콩 빼서 간식 추천해줘",
        "땅콩빼고 간식 추천해줘",
        "땅콩 뺀 간식 추천해줘",
    ],
)
def test_빼_달라는_말은_어미가_달라도_요청한_음식이_아니다(text):
    assert request_notices([text], [entry("땅콩")], stage="preschool", months=50) == ()
