"""글의 음식 낱말을 등록 알레르기 · 연령 규칙과 대조하는 `check_foods` 검증.

섭취 확인 답 · 식단 추천 요청 안내 · reason 검사가 이 판정 하나를 같이 쓴다. 모델을 부르지 않는다.
"""

import pytest

from app.agents.food.food_check import (
    check_foods,
    label_text,
    mentions_blocked_food,
    request_notices,
)
from app.agents.food.store.ports import SafetyEntry


def entry(label: str, kind: str = "allergy", status: str = "active") -> SafetyEntry:
    return SafetyEntry(kind=kind, label=label, status=status)


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


# ── /code-review 1 — 연령 규칙은 어절이 아니라 낱말마다 본다 ─────────────────────────
def ages(text, stage, months):
    return {
        v.word: (v.age_block.term.key if v.age_block else None, v.caution)
        for v in check_foods(text, [], stage=stage, months=months)
    }


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
