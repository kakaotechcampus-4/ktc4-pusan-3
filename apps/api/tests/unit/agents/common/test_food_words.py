"""글의 음식 낱말을 등록 알레르기 · 연령 규칙과 대조하는 `check_foods` 검증.

섭취 확인 답 · 식단 추천 요청 안내 · reason 검사가 이 판정 하나를 같이 쓴다. 모델을 부르지 않는다.
판정은 common 에 있고 Food 의 문구(`food_check`)는 `agents/food/test_food_check.py` 가 본다.
등록 행은 `SafetyRow` 모양이면 된다 — Food 의 포트를 import 하지 않는다.
"""

from dataclasses import dataclass

import pytest

from app.agents.common.food_words import (
    ITEM,
    QUESTION,
    SCAN,
    SENTENCE,
    check_foods,
    find_food_words,
    mentions_blocked_food,
)


@dataclass(frozen=True)
class Entry:
    """`health_safety` 한 행에서 판정이 읽는 칸(kind · label · status)."""

    kind: str
    label: str
    status: str = "active"


def entry(label: str, kind: str = "allergy", status: str = "active") -> Entry:
    return Entry(kind=kind, label=label, status=status)


def summary(verdicts):
    return [
        (
            v.word,
            tuple(c.entry.label for c in v.conflicts),
            tuple(sorted(set().union(*(c.codes for c in v.conflicts)))),
            v.age_block.term.key if v.age_block else None,
            v.caution,
        )
        for v in verdicts
    ]


NUT = [entry("견과류")]
CASES = [  # (등록, 글, 단계, 월령, 기대)
    (NUT, "땅콩소스 먹여도 될까?", "preschool", 50, [("땅콩", ("견과류",), (4,), None, None)]),
    (
        NUT,
        "견과류 들어간 메뉴 추천해줘",
        "preschool",
        50,
        [("견과류", ("견과류",), (4, 14, 19), None, None)],
    ),
    (
        [entry("호두")],
        "견과류 먹여도 돼?",
        "preschool",
        50,
        [("견과류", ("호두",), (14,), None, None)],
    ),
    (
        [entry("땅콩 알레르기")],
        "견과류 먹여도 돼?",
        "preschool",
        50,
        [("견과류", ("땅콩 알레르기",), (4,), None, None)],
    ),
    ([entry("호두")], "땅콩소스 먹여도 될까?", "preschool", 50, [("땅콩", (), (), None, None)]),
    (NUT, "아몬드 먹여도 돼?", "preschool", 50, [("아몬드", ("견과류",), (), None, None)]),
    (NUT, "밤 먹여도 돼?", "preschool", 50, [("밤", ("견과류",), (), None, None)]),
    (NUT, "밤에 우유 먹여도 돼?", "preschool", 50, [("우유", (), (), None, None)]),
    (NUT, "은행에 다녀와서 간식 먹여도 돼?", "preschool", 50, []),
    (NUT, "땅콩호박 수프 먹여도 돼?", "preschool", 50, [("수프", (), (), None, None)]),
    (
        [entry("용과")],
        "용과 먹여도 돼?",
        "preschool",
        50,
        [("용과", ("용과",), (), None, None)],
    ),
    (
        [entry("새우")],
        "해산물 먹여도 돼?",
        "preschool",
        50,
        [("해산물", ("새우",), (9,), None, None)],
    ),
    (
        [entry("유당불내증", kind="chronic_disease")],
        "유당불내증인데 우유 먹여도 돼?",
        "preschool",
        50,
        [("우유", ("유당불내증",), (2,), None, None)],
    ),
    (
        [entry("견과류", status="retracted")],
        "땅콩소스 먹여도 될까?",
        "preschool",
        50,
        [("땅콩", (), (), None, None)],
    ),
    ([], "꿀 넣은 이유식 먹여도 돼?", "infant_weaning", 8, [("꿀", (), (), "꿀", None)]),
    ([], "꿀 넣은 이유식 먹여도 돼?", "toddler", 20, [("꿀", (), (), None, None)]),
    ([], "우유 먹여도 돼?", "infant_weaning", 8, [("우유", (), (), "생우유", None)]),
    ([], "팝콘 먹여도 돼?", "toddler", 30, [("팝콘", (), (), "질식 위험 식품", None)]),
    ([], "땅콩 먹여도 돼?", "toddler", 30, [("땅콩", (), (), None, "질식 주의 식품")]),
    ([], "땅콩소스 먹여도 돼?", "toddler", 30, [("땅콩", (), (), None, None)]),
    ([], "마라탕 먹여도 돼?", "preschool", 50, []),
    ([], "이거 먹여도 돼?", "preschool", 50, []),
]


# ── 일상어는 음식 낱말이 아니다 (리뷰: 글 스캔이 메뉴 이름용 사전을 그대로 쓰던 문제) ─────────────
def words_of(text, entries, stage="preschool", months=50):
    return [v.word for v in check_foods(text, entries, stage=stage, months=months)]


EVERYDAY = [  # (등록, 글, 단계, 월령, 음식으로 잡히면 안 되는 낱말)
    ([entry("땅콩")], "이거 어떻게 먹여도 돼?", "preschool", 50, "게"),
    ([entry("새우")], "많이 먹게 해도 돼?", "preschool", 50, "게"),
    (NUT, "설사가 잦은데 우유 먹여도 돼?", "preschool", 50, "잦"),
    (NUT, "밤잠을 설쳐서 우유 먹여도 돼?", "preschool", 50, "밤"),
    (NUT, "낮밤이 바뀌었는데 우유 먹여도 돼?", "preschool", 50, "밤"),
    (NUT, "오늘 밤 우유 먹여도 돼?", "preschool", 50, "밤"),
    (NUT, "밤 늦게 우유 먹여도 돼?", "preschool", 50, "밤"),
    (NUT, "밤이 늦었는데 우유 먹여도 돼?", "preschool", 50, "밤"),
    (NUT, "밤 열 시에 우유 먹여도 돼?", "preschool", 50, "밤"),
    (NUT, "은행 갔다 와서 간식 먹여도 돼?", "preschool", 50, "은행"),
    ([entry("참깨")], "바나나 으깨서 먹여도 돼?", "preschool", 50, "깨"),
    ([entry("밀")], "장난감을 밀어서 놀았는데 간식 먹여도 돼?", "preschool", 50, "밀"),
    ([entry("밀")], "비밀 레시피로 만든 간식 먹여도 돼?", "preschool", 50, "밀"),
    ([entry("새우")], "우유에 대하여 물어도 돼?", "preschool", 50, "대하"),
    ([entry("생선")], "조기 이유식 시작해도 돼?", "preschool", 50, "조기"),
    ([entry("돼지고기")], "포크로 찍어서 먹여도 돼?", "preschool", 50, "포크"),
    ([], "구미에 가는 길에 간식 먹여도 돼?", "toddler", 30, "구미"),
    ([entry("쇠고기")], "안심하고 먹여도 돼?", "preschool", 50, "안심"),
    ([entry("생선")], "대구에 다녀와서 간식 먹여도 돼?", "preschool", 50, "대구"),
    # 등록한 한 글자 이름 — 일상어 한복판에서는 겹치지 않는다
    ([entry("배")], "배탈 났는데 죽 먹여도 돼?", "preschool", 50, "배"),
    ([entry("배")], "배고파해서 간식 먹여도 돼?", "preschool", 50, "배"),
    ([entry("배")], "배부르게 먹었는데 더 먹여도 돼?", "preschool", 50, "배"),
    ([entry("감")], "감기 걸렸는데 죽 먹여도 돼?", "preschool", 50, "감"),
    ([entry("마")], "마트에서 산 과자 먹여도 돼?", "preschool", 50, "마"),
    ([entry("마")], "물 마시고 나서 간식 먹여도 돼?", "preschool", 50, "마"),
    ([entry("조")], "조심해서 먹여도 돼?", "preschool", 50, "조"),
    ([entry("무")], "무슨 과자 먹여도 돼?", "preschool", 50, "무"),
    ([entry("소")], "소화가 안 되는데 죽 먹여도 돼?", "preschool", 50, "소"),
    ([entry("양")], "양이 적은데 더 먹여도 돼?", "preschool", 50, "양"),
    ([entry("알")], "알레르기 검사 전에 간식 먹여도 돼?", "preschool", 50, "알"),
    # 낱말 그대로 훑는 별칭도 일상어 합성어에서는 잡지 않는다 (/code-review 3)
    ([], "새싹반에서 준 간식 먹여도 돼?", "toddler", 30, "새싹"),
    ([], "엄마 껌딱지라 간식 먹여도 돼?", "toddler", 30, "껌"),
    ([], "꿀잠 자고 일어나서 간식 먹여도 돼?", "infant_weaning", 8, "꿀"),
    ([], "꿀팁 하나 듣고 간식 먹여도 돼?", "infant_weaning", 8, "꿀"),
    ([entry("쌀")], "쌀쌀한 날씨에 간식 먹여도 돼?", "preschool", 50, "쌀"),
    ([], "소주제를 정하고 간식 먹여도 돼?", "preschool", 50, "소주"),
    ([entry("사과")], "사과했는데 간식 먹여도 돼?", "preschool", 50, "사과"),
    ([entry("게")], "대게 아이들은 잘 먹던데 먹여도 돼?", "preschool", 50, "대게"),
]


# ── /code-review 1 — 연령 규칙은 어절이 아니라 낱말마다 본다 ─────────────────────────
def ages(text, stage, months):
    return {
        v.word: (v.age_block.term.key if v.age_block else None, v.caution)
        for v in check_foods(text, [], stage=stage, months=months)
    }


@pytest.mark.parametrize(("entries", "text", "stage", "months", "expected"), CASES)
def test_글의_음식_낱말을_등록_알레르기_연령_규칙과_대조한다(
    entries, text, stage, months, expected
):
    assert summary(check_foods(text, entries, stage=stage, months=months)) == expected


@pytest.mark.parametrize(
    ("reason", "blocked"),
    [
        ("고소한 견과류가 들어가 아이가 좋아할 거예요", True),
        ("아몬드를 조금 뿌려 주면 더 고소해요", True),
        ("땅콩호박으로 부드럽게 만든 수프예요", False),
        ("멸치볶음으로 짭조름하게 챙겨요", False),
    ],
)
def test_막힌_음식을_말하는지_본다(reason, blocked):
    assert mentions_blocked_food(reason, NUT, stage="preschool", months=50) is blocked


def test_12개월_전_꿀을_말하면_막힌_음식이다():
    assert mentions_blocked_food("꿀을 조금 넣으면 달콤해요", [], stage="infant_weaning", months=8)


@pytest.mark.parametrize(("entries", "text", "stage", "months", "stray"), EVERYDAY)
def test_일상어는_음식_낱말로_잡지_않는다(entries, text, stage, months, stray):
    verdicts = check_foods(text, entries, stage=stage, months=months)
    assert stray not in [v.word for v in verdicts]
    assert not any(v.blocked for v in verdicts)


@pytest.mark.parametrize(
    ("entries", "text"),
    [
        ([entry("게")], "게 먹여도 돼?"),
        ([entry("게")], "꽃게 먹여도 돼?"),
        ([entry("배")], "배 먹여도 돼?"),
        ([entry("배")], "배를 갈아서 먹여도 돼?"),
        ([entry("감")], "감 먹여도 돼?"),
        (NUT, "밤 먹여도 돼?"),
        (NUT, "밤 들어간 간식 먹여도 돼?"),
        (NUT, "밤을 넣어 만든 죽 먹여도 돼?"),
        ([entry("생선")], "조기 먹여도 돼?"),
    ],
)
def test_음식으로_쓰인_같은_낱말은_계속_잡는다(entries, text):
    assert any(v.conflicts for v in check_foods(text, entries, stage="preschool", months=50))


@pytest.mark.parametrize(
    ("entries", "reason", "stage", "months"),
    [
        ([entry("게")], "여러 식품군을 고르게 먹을 수 있어요", "preschool", 50),
        ([entry("게")], "새롭게 시도해 보기 좋아요", "preschool", 50),
        ([entry("게")], "함께 먹게 하면 좋아요", "preschool", 50),
        (NUT, "자주 먹어도 부담이 적고 잦은 간식으로도 좋아요", "preschool", 50),
        ([entry("참깨")], "낮잠에서 깨고 나서 먹기 좋은 간식", "preschool", 50),
        ([], "아이 구미에 맞는 간식이에요", "toddler", 30),
        ([entry("밀")], "비밀 없이 편하게 먹을 수 있어요", "preschool", 50),
        ([entry("배")], "배부르게 먹기 좋은 메뉴예요", "preschool", 50),
    ],
)
def test_흔한_추천_이유_문장은_막힌_음식으로_보지_않는다(entries, reason, stage, months):
    assert mentions_blocked_food(reason, entries, stage=stage, months=months) is False


def test_게를_곁들였다는_이유는_게_알레르기_아이에게_막힌_음식이다():
    assert mentions_blocked_food(
        "게를 곁들여 고소해요", [entry("게")], stage="preschool", months=50
    )


def test_합성어의_낱말은_자기_연령_규칙만_받는다():
    assert ages("꿀떡 먹여도 돼?", "toddler", 30) == {"꿀": (None, None), "떡": ("떡", None)}
    assert ages("꿀떡 먹여도 돼?", "infant_weaning", 8) == {"꿀": ("꿀", None), "떡": ("떡", None)}
    assert ages("포도젤리 먹여도 돼?", "toddler", 30) == {
        "포도": (None, "질식 주의 식품"),
        "젤리": ("질식 위험 식품", None),
    }


@pytest.mark.parametrize(
    ("entries", "text", "stage", "months"),
    [
        ([], "꿀물 먹여도 돼?", "infant_weaning", 8),
        ([entry("쌀")], "쌀과자 먹여도 돼?", "preschool", 50),
        ([entry("사과")], "사과즙 먹여도 돼?", "preschool", 50),
    ],
)
def test_가드를_더해도_합성어_음식은_계속_잡는다(entries, text, stage, months):
    assert any(v.blocked for v in check_foods(text, entries, stage=stage, months=months))


def test_알레르기를_말한_음식은_물은_음식이_아니다():
    peach = [entry("복숭아")]
    assert words_of("복숭아 알레르기 있는데 사과 먹여도 돼?", peach) == ["사과"]
    again = check_foods(
        "복숭아 알레르기 있는데 복숭아 먹여도 돼?", peach, stage="preschool", months=50
    )
    assert [v.word for v in again if v.conflicts] == ["복숭아"]


def test_추천_이유는_없이라고_써도_등록한_음식_이름이_나가면_막는다():
    # 질문은 빼 달라는 음식을 판정하지 않지만, 추천 이유는 화면에 나가는 글이라 이름 자체를 센다
    assert mentions_blocked_food(
        "땅콩 없이 만든 메뉴예요", [entry("땅콩")], stage="preschool", months=50
    )


# ── /code-review 5 — 피한 음식을 근거로 단 이유는 그 음식을 말해도 된다 ─────────────────
def test_허용한_낱말은_막힌_음식으로_세지_않는다():
    fish = [entry("생선")]
    reason = "생선을 싫어하는 기록이 있어 골랐어요"
    assert mentions_blocked_food(reason, fish, stage="preschool", months=50)
    assert not mentions_blocked_food(reason, fish, stage="preschool", months=50, allow=("생선",))


def test_추천_이유의_합성어에_든_알레르기_음식도_막는다():
    assert mentions_blocked_food(
        "흰우유와 함께 먹기 좋아요", [entry("우유")], stage="preschool", months=50
    )


@pytest.mark.parametrize(
    ("reason", "stage", "months"),
    [
        ("꿀처럼 달콤한 고구마라 잘 먹어요", "infant_weaning", 10),
        ("떡처럼 쫀득하지 않아 먹기 편해요", "toddler", 30),
        ("꿀같이 달콤한 고구마예요", "infant_weaning", 10),
    ],
)
def test_연령_금지_음식에_빗댄_말은_막힌_음식이_아니다(reason, stage, months):
    assert mentions_blocked_food(reason, [], stage=stage, months=months) is False


def test_알레르기_음식에_빗댄_말은_그래도_막는다():
    # 등록한 알레르기 이름은 빗댄 말이어도 화면에 나간다
    assert mentions_blocked_food(
        "땅콩처럼 고소한 맛이에요", [entry("땅콩")], stage="preschool", months=50
    )


@pytest.mark.parametrize(
    "text",
    ["땅콩빼빼로 먹여도 돼?", "땅콩 빼빼로 먹여도 돼?"],
)
def test_빼빼로는_빼_달라는_말이_아니다(text):
    verdicts = check_foods(text, [entry("땅콩")], stage="preschool", months=50)
    assert any(v.conflicts for v in verdicts)


@pytest.mark.parametrize(
    ("registered", "text"),
    [
        ([entry("게")], "이거 어떻게 먹여도 돼?"),
        ([entry("참깨")], "바나나 으깨서 먹여도 돼?"),
        ([entry("밀")], "잘 밀어서 먹여도 돼?"),
        ([entry("새우")], "많이 먹게 해도 돼?"),
    ],
)
def test_먹이는_말_앞의_일상어는_메뉴_필터로_다시_보지_않는다(registered, text):
    verdicts = check_foods(text, registered, stage="preschool", months=50)
    assert not any(v.blocked for v in verdicts)


# ── 쓰는 곳마다 다른 규칙 — 질문 · 문장 · 항목 · 스캔 (#582) ──────────────────────────────────────
def conflicts_of(text, label, rules, *, stage="preschool", months=50):
    return [v for v in check_foods(text, [entry(label)], stage=stage, months=months, rules=rules)
            if v.conflicts]  # fmt: skip


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("생선", "대구로 전 부쳐 보기"),
        ("생선", "대구에 밀가루 묻혀 구워 보기"),
        ("사과", "사과하고 배로 꼬치 만들기"),
    ],
)
def test_문장_규칙은_질문_규칙이_놓치는_음식_활동을_막는다(label, text):
    """질문 규칙은 헷갈리는 낱말(대구)을 먹이는 말이 곁에 있을 때만 음식으로 보고, 사과하 는 열린
    꼴이라 "사과하고" 를 덮는다. 문장 규칙은 #556 이 정한 guard 를 그대로 쓴다."""
    assert conflicts_of(text, label, SENTENCE)


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("감자", "장난감 자동차 굴리기"),
        ("육류", "인형 두고 기어서 가져오기"),
        ("생선", "입 다물고 기다리기"),
    ],
)
def test_문장_규칙은_띄어쓰기를_넘어_찾지_않는다(label, text):
    assert conflicts_of(text, label, SENTENCE) == []
    # 질문 규칙은 넘는다 — 띄어 쓴 이름("달 걀")을 놓치지 않으려는 것이다
    assert conflicts_of(text, label, QUESTION)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("밤을 구워 보기", True),
        ("오늘 밤 별 보기", False),
        ("밤하늘 별 보기", False),
        ("밤이 늦어서 잠자기", False),
    ],
)
def test_문장_규칙은_헷갈리는_낱말을_다루는_말이_곁에_있을_때만_음식으로_본다(text, found):
    assert bool(conflicts_of(text, "견과류", SENTENCE)) is found


@pytest.mark.parametrize(
    ("label", "item"),
    [("난류", "어묵"), ("난류", "튀김"), ("난류", "너겟"), ("땅콩", "견과류"), ("견과류", "밤")],
)
def test_항목_규칙은_메뉴_판정으로_막는다(label, item):
    """`menu_codes` 만 보면 제품마다 다른 성분(어묵 · 튀김 · 너겟의 달걀)이 통과한다. 한 글자 낱말
    ("밤")도 항목 하나는 그 음식이다 — 곁에 먹이는 말이 없어도 본다."""
    assert any(
        v.blocked
        for v in check_foods(item, [entry(label)], stage="preschool", months=50, rules=ITEM)
    )


def test_항목_규칙은_연령_규칙을_본다():
    [verdict] = check_foods("꿀", [], stage="infant_weaning", months=10, rules=ITEM)
    assert verdict.age_block is not None


def test_사전에_없는_음식은_판정이_비어_있다():
    """🚨 빈 결과는 "괜찮다" 가 아니라 "확인 못 함" 이다. 부르는 쪽이 정한다 — Activity 는 활성 음식
    알레르기가 있으면 그 후보를 뺀다."""
    assert check_foods("마라탕", [entry("난류")], stage="preschool", months=50, rules=ITEM) == ()


def test_스캔_규칙은_요리_이름을_찾고_띄어쓰기를_넘지_않는다():
    assert find_food_words("쿠키 반죽 빚어 굽기", SCAN) == ("쿠키",)
    assert find_food_words("새 우산 쓰고 산책하기", SCAN) == ()


@pytest.mark.parametrize("text", ["케이크 먹여도 돼?", "머핀 먹여도 돼?", "팬케이크 먹여도 돼?"])
def test_난류_아이에게_케이크류가_겹친다(text):
    """섭취 확인에서도 케이크가 난류에 안 걸려 "겹치지 않는다" 로 나왔다 (#582)."""
    assert conflicts_of(text, "난류", QUESTION)


def test_스캔_규칙은_19종_별칭을_곁의_말이_아니라_guard_로_가른다():
    """돼지 · 전복은 19종 스캔이 guard 와 함께 강하게 잡는다. 약한 낱말로 한 번 더 보면 guard 를
    못 읽어 "돼지 저금통 만들기" 가 걸린다. 음식 이름 안의 돼지(돼지국밥)는 계속 걸린다
    (#582 리뷰)."""
    assert find_food_words("종이로 돼지 저금통 만들기", SCAN) == ()
    assert find_food_words("돼지국밥 그림 그리기", SCAN) == ("돼지",)


# ── 흔한 재료 — 사전이 몰라서 "확인 못 함" 이던 항목 (#582 리뷰) ──────────────────────
COMMON = [
    "김", "귤", "오이", "배", "감", "브로콜리", "시금치", "양배추", "미역", "블루베리",
    "소금", "설탕", "물", "얼음", "배추", "대파",
]  # fmt: skip


@pytest.mark.parametrize("item", COMMON)
def test_항목_규칙은_흔한_재료를_막히지_않은_판정으로_낸다(item):
    """땅콩만 등록한 아이에게서 귤 · 오이 활동이 "확인 못 함" 으로 다 빠졌다 — 사전이 알레르기 ·
    연령 규칙에 걸리는 음식 위주라 흔한 재료를 몰랐다. 빈 튜플이 아니라 겹침 없는 판정이 나와야
    한다."""
    [verdict] = check_foods(item, [entry("땅콩")], stage="preschool", months=40, rules=ITEM)
    assert verdict.word == item
    assert not verdict.blocked


@pytest.mark.parametrize("item", ["귤", "오이", "배", "감", "김", "브로콜리"])
def test_항목_규칙은_흔한_재료도_등록한_아이에게는_막는다(item):
    verdicts = check_foods(item, [entry(item)], stage="preschool", months=40, rules=ITEM)
    assert any(v.conflicts for v in verdicts)


def test_배추는_배_알레르기로_막지_않는다():
    """한 글자 이름의 guard(배추 · 배달)가 흔한 재료 판정에서도 그대로 지켜진다."""
    [verdict] = check_foods("배추", [entry("배")], stage="preschool", months=40, rules=ITEM)
    assert not verdict.blocked


@pytest.mark.parametrize("item", ["미역국", "김치", "호박죽", "오이무침", "전복죽", "밤죽"])
def test_조합_음식은_흔한_재료로_막히지_않은_판정을_내지_않는다(item):
    """목록은 이름이 통째로 같을 때만 쓴다. 미역국의 미역이 알려졌다고 국 전체가 괜찮은 게 아니다 —
    김치는 젓갈, 호박죽은 쌀이 든다. 안에 든 재료를 몰라서 "확인 못 함" 으로 둔다."""
    assert check_foods(item, [entry("땅콩")], stage="preschool", months=40, rules=ITEM) == ()


@pytest.mark.parametrize(
    ("label", "item"),
    [
        ("과일", "귤"),
        ("과일", "블루베리"),
        ("채소", "오이"),
        ("채소 알레르기", "시금치"),
        ("감귤류", "귤"),
        ("해조류", "김"),
        ("해조류", "미역"),
        ("먼지", "귤"),  # 식품 아닌 알레르기 표에 없는 이름은 음식일 수 있어서 읽지 못한 걸로 본다
    ],
)
def test_분류_이름으로_등록한_아이에게는_흔한_재료도_확인_못_함이다(label, item):
    """ "과일" · "채소" 는 보호자가 넓게 막으려고 쓰는 묶음 이름이다. 사전이 읽지 못하는 알레르기가
    있으면 귤이 그 묶음에 드는지 알 수 없어서 막히지 않았다고 말하지 않는다(전처럼 빈 튜플)."""
    assert check_foods(item, [entry(label)], stage="preschool", months=40, rules=ITEM) == ()


@pytest.mark.parametrize(
    ("labels", "item"),
    [
        (["땅콩"], "귤"),
        (["땅콩", "우유"], "오이"),
        (["배"], "귤"),  # 흔한 재료 하나의 이름이다 — 다른 재료로 넓히지 않는다
        (["귤"], "오이"),
        (["땅콩, 귤"], "오이"),
        ([], "귤"),
    ],
)
def test_사전이_읽는_알레르기만_등록했으면_흔한_재료는_막히지_않는다(labels, item):
    entries = [entry(label) for label in labels]
    [verdict] = check_foods(item, entries, stage="preschool", months=40, rules=ITEM)
    assert not verdict.blocked


@pytest.mark.parametrize(
    "labels",
    [
        ["땅콩", "꽃가루"],
        ["꽃가루"],
        ["라텍스"],
        ["고양이털"],
        ["땅콩, 꽃가루"],
        ["우유", "동물털"],
    ],
)
def test_식품이_아닌_알레르기를_같이_등록해도_흔한_재료는_막히지_않는다(labels):
    """꽃가루 · 라텍스 · 동물털은 음식 묶음 이름이 아니다. Activity 가 음식 알레르기를 가를 때 쓰는
    식품 아닌 알레르기 표(`allergy_related_terms.yaml`)와 같은 기준이라, 땅콩과 꽃가루를 같이
    등록한 아이도 귤 활동이 빠지지 않는다 (#743 과 합쳐 보고 어긋난 것)."""
    entries = [entry(label) for label in labels]
    [verdict] = check_foods("귤", entries, stage="preschool", months=40, rules=ITEM)
    assert not verdict.blocked


def test_알레르기가_아닌_등록은_흔한_재료_판정을_막지_않는다():
    """환경 · 질환 등록(kind 가 allergy 가 아니다)은 읽을 수 없어도 음식 묶음 이름이 아니다."""
    entries = [entry("천식", kind="chronic_disease"), entry("꽃가루", kind="environmental")]
    [verdict] = check_foods("귤", entries, stage="preschool", months=40, rules=ITEM)
    assert not verdict.blocked


def test_참기름은_참깨_알레르기로_계속_막는다():
    """이 목록에 참기름 같은 가공 재료를 넣지 않는 이유다 — 사전이 이미 참깨로 안다."""
    verdicts = check_foods("참기름", [entry("참깨")], stage="preschool", months=40, rules=ITEM)
    assert any(v.conflicts for v in verdicts)


def test_흔한_재료_목록은_항목_규칙만_읽는다():
    """모든 아이의 후보에서 빼는 Growth 스캔이 읽으면 귤 까기 · 오이 썰기가 전원 손실이 된다.
    섭취 확인 · 추천 이유 · 문장 규칙도 읽지 않는다 — 문구가 달라지는 일이라 이번 범위가 아니다."""
    for item in COMMON:
        assert find_food_words(item, SCAN) == ()
    assert check_foods("귤 먹여도 돼?", [entry("땅콩")], stage="preschool", months=40) == ()
    assert check_foods("귤", [entry("땅콩")], stage="preschool", months=40, rules=SENTENCE) == ()


def test_흔한_재료는_사전이_이미_아는_이름이_아니다():
    """사전이 아는 이름(감자 · 양파 · 쌀 …)을 목록에 또 적으면 두 곳이 어긋난다. 사전이 아는 이름 ·
    그 이름이 든 이름(계란빵 · 땅콩쿠키 …)도 넣지 않는다."""
    from app.agents.common.food_words import WordRules
    from app.agents.common.reference import known_foods

    dictionary_only = WordRules(False, None, (), False, True)  # known 을 읽지 않는 항목 규칙
    names = known_foods()
    assert names
    assert [name for name in names if find_food_words(name, dictionary_only)] == []
