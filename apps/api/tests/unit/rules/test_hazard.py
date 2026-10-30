"""app/rules/hazard.py — 위험 물질 사전검사 (#626).

순수 함수만 검증한다. LLM·DB 없음.
"""

import pytest

from app.agents.supervisor.schemas import GuardReason
from app.rules.hazard import FOOD_NAME_TERMS, PLAY_NAME_TERMS, find_hazards, hazard_names
from app.rules.term_match import Term, match_spans, normalize
from tests.eval.agents.routing_cases import ALL_CASES


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 멘토 리뷰 입력 — 마약류는 이름만 나와도 건다
        ("아이가 필로폰을 했는데 좋아했다", ("drug",)),
        ("대마초 냄새를 좋아해", ("drug",)),
        ("마약을 먹은 것 같아", ("drug",)),
        ("소주를 마셨어", ("alcohol",)),
        ("술을 한 모금 먹었는데 좋아했어", ("alcohol",)),
        ("맥주 맛을 봤어", ("alcohol",)),
        ("락스를 한 모금 마셨어", ("chemical",)),
        ("세제 조금 먹은 것 같아", ("chemical",)),
        ("담배꽁초를 먹었어", ("tobacco",)),
        ("동전을 삼켰어", ("object",)),
        ("구슬을 입에 넣었어", ("object",)),
        ("밥 먹고 나서 건전지를 삼켰어", ("object",)),
        ("소주 마시고 동전도 삼켰어", ("alcohol", "object")),
        # 흔한 활용형 · 이름과 동사 사이의 낱말 (#626 리뷰)
        ("소주를 먹더니 좋아했어", ("alcohol",)),
        ("세제를 먹다가 뱉었어", ("chemical",)),
        ("맥주를 맛보더니 좋아했어", ("alcohol",)),
        ("농약 묻은 고기 먹었어", ("chemical",)),
        ("레고 삼켰어", ("object",)),  # "고" 로 끝나도 이름이면 마디를 끊지 않는다
        # 이름 앞 어른은 주어가 아니다
        ("아빠 맥주를 아이가 마셨어", ("alcohol",)),
        # 숟가락 수 guard 가 술 이름까지 덮지 않는다
        ("밥 한 술 먹고 소주도 마셨어", ("alcohol",)),
    ],
)
def test_위험_물질을_먹거나_마셨다는_말을_건다(text, expected) -> None:
    assert find_hazards(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "술래잡기 하고 간식 먹었어",
        "약과를 맛있게 먹었어",
        "술빵 먹었어",
        "단술 마셨어",
        "미술 시간에 우유 마셨어",
        "마약김밥 잘 먹었어",
        "마시멜로 구워 먹었어",
        # 이름과 동사가 다른 마디다
        "락스로 닦고 밥 먹었어",
        "동전 모으기 했어. 저녁은 김밥 먹었어",
        "세제 냄새가 싫대",
        # 낱말 안에 든 이름 (#626 리뷰)
        "입술을 핥았어",
        "사케동 먹었어",
        "무농약 딸기 먹었어",
        "맛술 넣은 불고기 먹었어",
        # 숟가락을 세는 "술" (#633 리뷰)
        "밥 두 술 먹었어",
        "아이가 밥을 한 술 먹었어",
        "죽을 두 술 먹었어",
        "국을 몇 술 떠먹더니 안 먹겠대",
        "한두 술 먹고 말았어",
        # 부정 · 금지 · 조건
        "소주는 안 마셨어",
        "유치원에서 술 마시면 안 된다고 배웠대",
        "세제는 먹지 마",
        # 어른이 주어인 마디
        "엄마는 와인 마시고 아이는 주스 마셨어",
        "아빠가 맥주를 마셨어",
        "동전 먹었어",  # 물건은 삼키다 · 입에 넣다만 본다 — 놓친 것은 Supervisor 가 받는다
        "아이가 마약 먹었어",  # "마약" 한 낱말은 마약김밥 때문에 안 본다 — Supervisor 가 받는다
        # 먹인 약은 위험 물질이 아니다 — 투약 기록은 그대로 저장된다 (#626 PM 리뷰)
        "열나길래 약먹였어",
        "자기 전에 감기약 시럽 먹였어",
        "해열제 5ml 먹였어",
        "",
    ],
)
def test_위험_물질이_아닌_말은_걸지_않는다(text) -> None:
    assert find_hazards(text) == ()


_PASSING = [case for case in ALL_CASES if all(s.guard != GuardReason.HAZARD for s in case.spans)]


@pytest.mark.parametrize("case", _PASSING, ids=[case.case_id for case in _PASSING])
def test_위험_물질_구간이_없는_eval_케이스는_규칙에_걸리지_않는다(case) -> None:
    # 오탐 대조군(RC31 · RC38~47)과 평범한 입력 전체 — 걸리면 Supervisor · Memory 를 못 탄다
    assert find_hazards(case.text) == ()


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["소주"], ("alcohol",)),
        (["필로폰"], ("drug",)),
        (["락스"], ("chemical",)),
        (["술빵"], ()),
        (["마약김밥"], ()),
        (["마약 김밥"], ()),  # 띄어 써도 guard 가 덮는다
        (["맛술 불고기"], ()),
        (["사케동"], ()),
        (["무알콜 맥주"], ()),
        (["딸기", "우유"], ()),
    ],
)
def test_음식_이름_칸의_위험_물질(names, expected) -> None:
    assert hazard_names(names, FOOD_NAME_TERMS) == expected


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["필로폰 하기"], ("drug",)),
        (["마약 예방 교육"], ()),
        (["술래잡기"], ()),
        (["동전 모으기"], ()),
        (["미술 놀이"], ()),
    ],
)
def test_놀이_배움_이름_칸은_마약류만_본다(names, expected) -> None:
    assert hazard_names(names, PLAY_NAME_TERMS) == expected


def test_match_spans_는_guard_가_덮지_않은_자리만_돌려준다() -> None:
    wheat = Term(key="6", aliases=("밀",), guards=("밀크",))
    assert match_spans(normalize("밀크티와 통밀빵"), (wheat,)) == (("6", 5, 6),)
