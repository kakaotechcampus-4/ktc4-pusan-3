"""Growth 요청 주제 사전 — 요청 · 기록 조각에서 문서 행 영역 키를 읽는다 (#646)."""

import pytest

from app.agents.growth.doc_seed import AREAS, ROUTINE_CATEGORIES, growth_doc_seed
from app.rules.growth_topic import (
    OTHER,
    ROUTINE_TERMS,
    TOPIC_TERMS,
    expand_keywords,
    on_topic,
    request_topic,
    routine_scope,
    strict_topic,
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


@pytest.mark.parametrize(
    ("texts", "areas", "expected"),
    [
        (("한글 자음 찾기",), ("communication",), True),
        (("이야기 이어 말해 보기",), ("communication",), True),
        (("공룡 이름 짝맞추기",), ("communication",), False),
        (("블록 세어 보기",), ("nature",), True),
        (("공룡 그리기",), ("art",), True),
        # 영역이 둘이면 하나만 맞아도 주제 안이다
        (("공룡 그리기",), ("communication", "art"), True),
        # 주제가 없으면 거르지 않는다
        (("아무거나",), (), True),
    ],
)
def test_후보_글에_주제_영역의_낱말이_있으면_주제_안이다(texts, areas, expected):
    assert on_topic(texts, areas) is expected


@pytest.mark.parametrize(
    ("request_texts", "record_texts", "scope"),
    [
        # 요청 조각에 행동이 없으면 기록 조각에서 읽는다
        (("어떻게 하면 좋을까?",), ("아이가 밥 먹을 때 너무 힘들어해",), ("mealtime",)),
        (("아침에 씻는 걸 혼자 하게",), (), ("self_care",)),
        # 요청 조각에 있으면 기록 조각의 다른 행동은 끼지 않는다
        (("양치 어떻게 가르쳐?",), ("밥은 잘 먹었어",), ("self_care",)),
        # 행동이 둘이면 둘 다 범위다
        (("밥 먹고 양치도 혼자 하게",), (), ("mealtime", "self_care")),
        (("어떻게 하면 좋을까?",), (), ()),
    ],
)
def test_루틴_카테고리를_요청_조각부터_읽는다(request_texts, record_texts, scope):
    assert routine_scope(request_texts, record_texts) == scope


def test_루틴_카테고리_키는_시드와_같다():
    assert {term.key for term in ROUTINE_TERMS} == ROUTINE_CATEGORIES


@pytest.mark.parametrize("area", ["communication", "nature"])
def test_시드_교육_행은_자기_영역의_후보_글로_통과한다(area):
    """후보 글로 거르는 영역은 시드 행이 자기 영역에서 빠지면 안 된다 — 좁으면 맞는 후보를 뺀다."""
    rows = [
        entry.to_row()
        for entry in growth_doc_seed()
        if entry.meta.status == "approved"
        and entry.to_row().row_type == "learning_activity"
        and entry.to_row().area == area
    ]
    assert rows
    failed = [r.title for r in rows if not on_topic((r.title, r.body, *r.materials), (area,))]
    assert failed == []


@pytest.mark.parametrize(
    ("request_texts", "record_texts", "areas"),
    [
        (("국어 공부를 어려워하는데",), (), ("communication",)),
        (("수학 공부 어떻게 도와줘?",), (), ("nature",)),
        # 요청 조각에 주제가 없으면 기록 조각의 주제를 쓴다(요청 주제 읽기와 같다)
        (("어떻게 도와줘야 좋을까?",), ("아이가 국어 공부를 어려워하는데",), ("communication",)),
        # 낱말이 후보 글을 다 덮지 못하는 영역이 섞이면 거르지 않는다
        (("그림책 읽어 주는 방법",), (), ()),
        (("친구랑 잘 놀게 하고 싶어",), (), ()),
        (("영어 공부 어떻게 시켜?",), (), ()),
        (("놀이 추천해줘",), (), ()),
    ],
)
def test_후보를_글로_거르는_영역은_낱말이_덮는_영역뿐이다(request_texts, record_texts, areas):
    assert strict_topic(request_texts, record_texts) == areas


@pytest.mark.parametrize(
    ("text", "scope"),
    [
        ("아침에 나갈 준비할 때 너무 오래 걸려", ("transition",)),
        ("코 파는 버릇 어떻게 고쳐?", ("habit",)),
        ("손가락 빨기 어떻게 고쳐?", ("habit",)),
    ],
)
def test_띄어쓴_루틴_낱말도_카테고리로_읽는다(text, scope):
    assert routine_scope((text,), ()) == scope
