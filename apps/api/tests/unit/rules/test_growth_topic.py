"""Growth 요청 주제 사전 — 요청 · 기록 조각에서 문서 행 영역 키를 읽는다 (#646)."""

import pytest

from app.agents.growth.doc_seed import AREAS
from app.rules.growth_topic import (
    OTHER,
    TOPIC_TERMS,
    expand_keywords,
    request_topic,
    topic_areas,
)


@pytest.mark.parametrize(
    ("texts", "areas"),
    [
        (("어떻게 도와줘야 좋을까?", "아이가 국어 공부를 어려워하는데"), ("communication",)),
        (("수학 공부를 어려워하는데 어떻게 도와줄까?",), ("nature",)),
        (("그림책 읽어 주는 방법 알려줘",), ("communication", "art")),
        (("영어 노래 추천해줘",), ("art", "other")),
        (("놀이 추천해줘",), ()),
        # 낱말 사이 띄어쓰기를 넘어 걸리면 안 된다 — "수영 어" 가 영어, "친구들과 학교" 가 과학
        (("수영 어떻게 가르쳐?",), ()),
        (("친구들과 학교 가는 길에",), ("social",)),
        (("책과 학습지 풀었어",), ()),
        (("동생도 형처럼 해요",), ()),
        (("어떻게 하면 좋을까?", "아이가 밥 먹을 때 너무 힘들어해"), ()),
        ((), ()),
    ],
)
def test_요청과_기록_조각에서_영역을_읽는다(texts, areas):
    assert topic_areas(texts) == areas


def test_영역_키는_문서_행_area_와_같다():
    keys = {term.key for term in TOPIC_TERMS}
    assert keys - {OTHER} <= AREAS
    assert len(keys) == len(TOPIC_TERMS)


def test_같은_영역_낱말로_넓힌다():
    wide = expand_keywords(["국어"])
    assert wide[0] == "국어" and "읽기" in wide and "수학" not in wide


def test_영역_밖_낱말은_그대로다():
    assert expand_keywords(["공룡"]) == ("공룡",)
    assert expand_keywords([]) == ()


def test_넓혀도_낱말은_한_번씩이다():
    wide = expand_keywords(["국어", "한글"])
    assert len(wide) == len(set(wide))


@pytest.mark.parametrize(
    ("request_texts", "record_texts", "areas"),
    [
        # 요청 조각에 주제가 없으면 기록 조각의 주제를 쓴다
        (("어떻게 도와줘야 좋을까?",), ("아이가 국어 공부를 어려워하는데",), ("communication",)),
        # 요청 조각에 주제가 있으면 기록 조각의 다른 주제는 끼지 않는다
        (("한글 놀이 추천해줘",), ("오늘 어린이집에서 노래 배웠대",), ("communication",)),
        # 영어만 물으면 영역 밖 주제로 남는다 — 예시 행이 없어 모델 전에 닫을 수 있다
        (("영어 공부 어떻게 시켜?",), (), ("other",)),
        # 기록 조각에만 있는 영역 밖 주제는 요청을 거르지 않는다
        (("집에서 뭐 하면 좋을까?",), ("영어 유치원 다니는데",), ()),
        # 기록 조각에서 읽을 때도 영역 밖 주제는 빼고 남은 영역만 쓴다
        (("어떻게 하지?",), ("영어 노래 배웠대",), ("art",)),
        ((), (), ()),
    ],
)
def test_요청_조각의_주제가_먼저고_기록_조각만의_영역_밖_주제는_거르지_않는다(
    request_texts, record_texts, areas
):
    assert request_topic(request_texts, record_texts) == areas
