"""Growth 요청 주제 사전 — 요청 · 기록 조각에서 문서 행 영역 키를 읽는다 (#646)."""

import pytest

from app.agents.growth.doc_seed import AREAS, ROUTINE_CATEGORIES, growth_doc_seed
from app.rules.growth_topic import (
    OTHER,
    ROUTINE_TERMS,
    SCREEN,
    TOPIC_DOC,
    TOPIC_TERMS,
    doc_areas,
    doc_tags,
    expand_keywords,
    on_topic,
    record_on_topic,
    request_topic,
    routine_scope,
    routine_words,
    row_on_topic,
    screen_use,
    strict_topic,
    topic_areas,
    topic_word,
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
    # 문서 행에 영역 키가 따로 없는 주제는 `TOPIC_DOC` 이 행의 영역 · 태그로 잇는다
    assert keys - {OTHER} - set(TOPIC_DOC) <= AREAS
    assert {area for area, _ in TOPIC_DOC.values()} <= AREAS
    assert len(keys) == len(TOPIC_TERMS)
    tagged = {
        tag
        for entry in growth_doc_seed()
        if entry.meta.status == "approved"
        for tag in entry.to_row().tags
    }
    assert {tag for _, tag in TOPIC_DOC.values()} <= tagged


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


SCREEN_CONCERN = ("아이가 요새 게임만 자꾸 하려고 하는데",)


@pytest.mark.parametrize(
    ("request_texts", "record_texts"),
    [
        (("어떻게 해야해?",), SCREEN_CONCERN),
        (("요즘 유튜브를 너무 많이 봐.",), ()),
        (("TV 를 너무 오래 봐",), ()),
        (("스마트폰만 들여다봐서 걱정이야",), ()),
    ],
)
def test_화면_낱말을_주제로_읽는다(request_texts, record_texts):
    assert request_topic(request_texts, record_texts) == (SCREEN,)


@pytest.mark.parametrize(
    "text",
    [
        "보드게임 하고 놀았어",
        "카드 게임 추천해줘",
        "숫자 게임 해 볼까",
        "영상 통화로 할머니랑 얘기했어",
        "퍼즐 게임 추천해줘",
        "빙고 게임 하고 싶어해요",
        "끝말잇기 게임 어떻게 가르쳐요",
        "숨바꼭질 게임 규칙을 알려줘",
    ],
)
def test_놀이_이름의_게임은_화면이_아니다(text):
    assert SCREEN not in topic_areas((text,))


def test_루틴_낱말이_있으면_화면이어도_루틴이다():
    assert screen_use(("어떻게 해야해?",), SCREEN_CONCERN) is True
    assert screen_use(("양치할 때 유튜브 틀어 줘야 해",), ()) is False
    assert screen_use(("밥 먹을 때 영상을 틀어 줘야 먹어",), ()) is False
    assert screen_use(("어떻게 해야해?",), ()) is False


def test_화면_주제는_신체운동_건강_행에서_screen_태그로_고른다():
    assert doc_areas((SCREEN,)) == ("physical_health",)
    assert doc_tags((SCREEN,)) == ("screen",)
    assert doc_areas(("communication",)) == ("communication",)
    assert doc_tags(("communication",)) == ()
    # 태그가 없는 주제가 섞이면 태그로 좁히지 않는다 — 그 영역 행이 빠진다
    assert doc_areas((SCREEN, "communication")) == ("physical_health", "communication")
    assert doc_tags((SCREEN, "communication")) == ()
    assert doc_areas(()) == () and doc_tags(()) == ()


def test_문서_행이_주제_안인지_영역과_태그로_본다():
    assert row_on_topic("physical_health", ("physical_health", "screen"), (SCREEN,))
    assert not row_on_topic("physical_health", ("physical_health", "rule"), (SCREEN,))
    assert not row_on_topic("communication", ("screen",), (SCREEN,))
    assert row_on_topic("communication", ("communication",), ("communication", SCREEN))
    assert not row_on_topic(None, (), ("communication",))


def test_시드_화면_행은_화면_주제의_후보_글로_통과한다():
    rows = [
        entry.to_row()
        for entry in growth_doc_seed()
        if entry.meta.status == "approved" and "screen" in entry.to_row().tags
    ]
    assert rows
    failed = [r.title for r in rows if not on_topic((r.title, r.body, *r.materials), (SCREEN,))]
    assert failed == []


def test_화면_후보는_화면_낱말이나_약속_낱말이_있어야_한다():
    assert on_topic(("게임 끝나면 블록 쌓기",), (SCREEN,))
    assert on_topic(("끝나는 시간 모래시계로 정하기",), (SCREEN,))
    assert not on_topic(("신호등 놀이",), (SCREEN,))
    assert not on_topic(("카드 게임 따라 하기",), (SCREEN,))


def test_화면은_후보_글로_거르는_영역이다():
    assert strict_topic(("어떻게 해야해?",), SCREEN_CONCERN) == (SCREEN,)


@pytest.mark.parametrize(
    ("names", "area", "expected"),
    [
        # 기록 이름은 주제 낱말로만 읽는다 — 후보 글의 활동 낱말(약속 · 규칙 · 끄기)은 쓰지 않는다
        (("약속 지키기",), "screen", False),
        (("불 끄기",), "screen", False),
        (("규칙 지키기",), "nature", False),
        (("순서 기다리기",), "nature", False),
        (("게임",), "screen", True),
        (("휴대폰 게임",), "screen", True),
        (("글 읽기", "4문장 이상 글 읽기"), "communication", True),
        (("숫자",), "nature", True),
        (("영어",), "other", True),
        (("블록 쌓기",), "communication", False),
    ],
)
def test_기록_이름은_주제_낱말로만_주제_안인지_본다(names, area, expected):
    assert record_on_topic(names, (area,)) is expected


def test_주제가_없으면_기록은_늘_주제_안이다():
    assert record_on_topic(("블록 쌓기",), ()) is True


class TestRoutineWords:
    """보호자가 말한 루틴 낱말 — 자료 없는 루틴 행동의 개인화 근거를 이름으로 맞출 때 쓴다."""

    def test_요청에서_걸린_루틴_낱말을_돌려준다(self):
        assert routine_words(("아침에 등원 준비가 너무 오래 걸려.",), ()) == ("등원",)

    def test_여러_낱말은_나온_순서로_한_번씩(self):
        assert routine_words(("양치하고 손 씻기 혼자 하게 하고 싶어",), ()) == ("양치", "씻")

    def test_요청에_없을_때만_기록_조각을_읽는다(self):
        assert routine_words(("어떻게 하면 좋을까?",), ("아이가 밥 먹을 때 힘들어해",)) == (
            "밥",
            "먹",
        )
        assert routine_words(("등원 준비가 힘들어",), ("아이가 밥 먹을 때 힘들어해",)) == ("등원",)

    def test_루틴_낱말이_없으면_빈_튜플이다(self):
        assert routine_words(("놀이 추천해줘",), ("공룡을 좋아해",)) == ()

    def test_요청_범위와_같이_비거나_채워진다(self):
        for texts in (("등원 준비가 힘들어",), ("영어 어떻게 가르쳐?",), ("양치 싫어해",)):
            assert bool(routine_words(texts, ())) == bool(routine_scope(texts, ()))


class TestTopicWord:
    """질문 문장에 넣는 주제 낱말 — 요청 조각이 먼저고, 없을 때만 기록 조각이다."""

    @pytest.mark.parametrize(
        ("request_texts", "record_texts", "word"),
        [
            (("국어 공부 어떻게 도와줘?",), (), "국어"),
            (("어떻게 도와줘야 좋을까?",), ("아이가 국어 공부를 어려워하는데",), "국어"),
            (("수학 공부를 어려워하는데 어떻게 도와줄까?",), (), "수학"),
            (("영어 어떻게 가르쳐?",), (), "영어"),
            (("그림책 읽어 주는 방법 알려줘",), (), "그림"),
            (("놀이 추천해줘",), ("공룡을 좋아해",), None),
            ((), (), None),
        ],
    )
    def test_요청에서_주제_낱말을_읽는다(self, request_texts, record_texts, word):
        assert topic_word(request_texts, record_texts) == word

    def test_활용형은_건너뛰고_다음_낱말을_본다(self):
        # "읽어" 는 질문 문장에 넣으면 어색하다 — 같은 요청의 다음 주제 낱말("그림")을 쓴다
        assert topic_word(("그림책 읽어 주기",), ()) == "그림"
        assert topic_word(("읽어 주기만 해",), ()) is None

    def test_기록_조각의_영어는_묻지_않는다(self):
        """ "영어 유치원 다니는데" 의 영어는 물은 것이 아니다 — request_topic 과 같은 기준."""
        assert topic_word(("집에서 뭐 하면 좋을까?",), ("영어 유치원 다니는데",)) is None
        assert topic_word(("영어 유치원 다니는데 뭐 하면 좋을까?",), ()) == "영어"

    def test_요청이_주제를_정했으면_기록_조각을_보지_않는다(self):
        assert topic_word(("수학 놀이 추천해줘",), ("오늘 노래 배웠대",)) == "수학"

    def test_주제_영역이_있으면_낱말도_있다(self):
        for texts in (("국어 어떻게 해?",), ("수학 어떻게 해?",), ("운동 어떻게 해?",)):
            assert bool(request_topic(texts, ())) == (topic_word(texts, ()) is not None)
