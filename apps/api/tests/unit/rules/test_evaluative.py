"""평가 표현 — 또래 비교 · 발달 판정은 거르고, 같은 글자가 든 평범한 문장은 통과시킨다."""

import pytest

from app.rules.evaluative import EVALUATIVE_PHRASES, find_evaluative


@pytest.mark.parametrize(
    "text",
    [
        "또래보다 블록을 잘 쌓아요",
        "또래에 비해 말이 빨라요",
        "평균보다 오래 집중했어요",
        "정상 발달 범위예요",
        "발달이 늦은 편이라 도움이 돼요",
        "소근육 발달 단계에 맞는 놀이",
        "그림에 재능이 있어요",
        "문제 행동을 줄여 줘요",
        "뛰어난 집중력",
    ],
)
def test_평가_표현을_잡는다(text):
    assert find_evaluative(text)


@pytest.mark.parametrize(
    "text",
    [
        "늦은 오후에 공원 산책",
        "느리게 걷기 놀이",
        "산 정상까지 걸어가기",
        "또래 친구와 공놀이",
        "밖으로 뛰어나가 비눗방울 잡기",
        "평소처럼 블록 쌓기",
        "문제 풀이 대신 그림 그리기",
    ],
)
def test_같은_글자가_든_평범한_문장은_통과한다(text):
    """RAG_plan 의 한 글자 어간(늦 · 느리 · 정상)을 그대로 걸면 이런 문장이 거절된다."""
    assert find_evaluative(text) == ()


def test_공백은_무시한다():
    assert find_evaluative("또래 보다 잘해요") == ("또래보다",)


def test_목록에_중복이_없다():
    dense = ["".join(p.split()) for p in EVALUATIVE_PHRASES]
    assert len(dense) == len(set(dense))
