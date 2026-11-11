"""메뉴명 → `menu_key` 정규화. 정본은 `food_agent_own_table.md` §1.
공백 · 괄호 · 급식 알레르기 번호를 지운다.

떼는 것은 읽는 것이 아니라 지우는 것이다 — 급식표의 알레르기 번호는 판정에 쓰지 않는다.
"""

import json
import random
from pathlib import Path

import pytest

from app.rules.menu_key import (
    is_nutrition_line,
    meal_menu_keys,
    menu_display_name,
    normalize_menu_key,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("된장찌개", "된장찌개"),
        ("된장 찌개", "된장찌개"),  # 공백
        (" 미역국 ", "미역국"),
        (
            "카레라이스(2.5.6)",
            "카레라이스",
        ),  # 급식표 알레르기 번호 — 안 떼면 카탈로그에서 못 찾는다
        ("닭갈비(5.6.15.)", "닭갈비"),
        ("우유 2,5", "우유"),
        ("우유②", "우유"),
        ("우유(200ml)", "우유"),  # 괄호
        ("두부조림[국산]", "두부조림"),
        ("3색나물", "3색나물"),  # 이름 속 숫자는 번호가 아니다
        ("ＫＩＭＣＨＩ볶음밥", "kimchi볶음밥"),  # 전각 영문, 소문자
    ],
)
def test_menu_key_정규화(raw: str, expected: str) -> None:
    assert normalize_menu_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "(2.5)", "①②", "---"])
def test_이름이_남지_않으면_빈_문자열(raw: str) -> None:
    # 빈 키는 호출부가 unresolved 로 둔다 — 빈 이름으로 외부를 부르지 않는다
    assert normalize_menu_key(raw) == ""


def test_같은_이름은_표기가_달라도_같은_키() -> None:
    assert normalize_menu_key("김치 볶음밥 (1.5)") == normalize_menu_key("김치볶음밥")


def test_다른_이름은_다른_키로_남는다() -> None:
    # 비슷한 이름으로 합치지 않는다 — 된장찌개와 된장찌개_두부는 다른 메뉴다
    assert normalize_menu_key("된장찌개") != normalize_menu_key("된장찌개 두부")


# ── 급식표 원문 → 화면 · 저장에 쓰는 메뉴 이름 (#908) ──
# 모델의 `name` 끝에 " Riot" · "/Optional?" · `}{"` 같은 찌꺼기가 붙어 화면 제목과 저장 키까지 갔다.
# 원문(`raw`)은 맞게 읽혔다 — 이름은 원문에서 코드가 만든다. 지우기만 하고 글자를 만들지 않는다.

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meal_plan_2025_01.json"


def _fixture_raws() -> list[str]:
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [it["raw"] for d in plan["days"] for m in d["meals"] for it in m["items"]]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("백김치9(백깍두기9)", "백김치(백깍두기)"),  # 대체 메뉴 괄호는 남긴다 — 모델 이름과 같다
        ("모듬버섯된장국5,6", "모듬버섯된장국"),
        ("치킨가스&소스1,5,6,15", "치킨가스&소스"),
        ("경기도과일 또는 고구마우유죽2", "경기도과일 또는 고구마우유죽"),
        ("연근조림★5,6", "연근조림"),  # 꾸밈 기호
        ("백미밥1/2", "백미밥1/2"),  # 분량은 번호가 아니다 (strip_allergen_marks 와 같다)
        ("3색나물", "3색나물"),
        ("카레라이스(2.5.6)", "카레라이스"),  # 번호만 있던 괄호는 빈 괄호로 남기지 않는다
        ("애호박국:⑤⑥", "애호박국"),
        # 아래는 모델이 원문 칸에 옮긴 군더더기 (11-11 측정 M1) — 메뉴 글자는 그대로다
        ("떡(증편),", "떡(증편)"),  # 칸 끝 쉼표
        ("빵(찐빵5,6,10,16,18),", "빵(찐빵)"),
        ("미니핫도그1,2,5,6,10,15,16,", "미니핫도그"),
        ("백김치9(백깍두기9)`", "백김치(백깍두기)"),  # 끝에 붙은 `
        ("빵(식빵2,5,6&&쨈),", "빵(식빵&쨈)"),  # & 가 겹쳐 읽힘
    ],
)
def test_메뉴_이름은_원문에서_번호와_기호를_뺀_것(raw: str, expected: str) -> None:
    assert menu_display_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        *_fixture_raws(),
        "백김치9(백깍두기9)`",
        "빵(식빵2,5,6&&쨈),",
        "떡(증편 Riot",  # 원문에 찌꺼기가 왔어도 지울 뿐 다른 글자를 넣지 않는다
        "(2.5)",
        "",
    ],
)
def test_메뉴_이름은_원문에_없는_글자를_담지_않는다(raw: str) -> None:
    name = menu_display_name(raw)

    assert set(name) <= set(raw)
    assert name == name.strip()


@pytest.mark.parametrize("raw", _fixture_raws())
def test_원문에서_만든_이름의_저장_키는_원문의_키와_같다(raw: str) -> None:
    # 이름을 다듬어도 저장 키(→ 카탈로그 · 알레르기 판정)가 원문과 다른 메뉴를 가리키지 않는다
    assert normalize_menu_key(menu_display_name(raw)) == normalize_menu_key(raw)


# ── 메뉴가 아닌 줄 — 열량 · 영양 표기 (2026-11-11 운영 장애 사진) ──
# 칸마다 "총 471 kcal" 줄이 있는 급식표가 있다. 프롬프트는 열량 행을 넣지 말라고 하지만
# 모델이 넣으면 원문에서 만든 이름이 "총 kcal" 가 되어 가짜 메뉴로 저장된다.
# 판별은 보수적이다 — 음식 이름을 잘못 버리면 영양 계산 · 메뉴 기억에서 빠진다.
# 열량 · 영양 낱말과 숫자 말고 아무 글자도 없을 때만이다.

# 시연용으로 지어낸 2026-11 급식표 정답셋(156항목)의 원문 — 기관 · 사람 이름 없음
DEMO_RAWS = (
    "가래떡구이",
    "갈치조림5,6",
    "감자국5",
    "감자된장국5,6",
    "감자샐러드모닝빵1,2,5,6",
    "감자전1,6",
    "감자채볶음",
    "고구마맛탕",
    "고등어구이7",
    "고등어무조림5,6,7",
    "귤",
    "근대된장국5,6",
    "깍두기9",
    "깍두기9(백깍두기9)",
    "누룽지죽",
    "단무지무침13",
    "달걀국1",
    "달걀말이1",
    "달걀찜1",
    "닭갈비5,6,15",
    "닭강정5,6,15",
    "닭고기채소볶음5,6,15",
    "닭다리살구이5,6,15",
    "닭봉간장조림5,6,15",
    "도라지나물",
    "동그랑땡1,5,6,10",
    "동태전1,6",
    "돼지고기김치찜5,9,10",
    "돼지고기수육10",
    "돼지고기장조림5,6,10",
    "돼지불고기5,6,10",
    "두부조림5,6",
    "들깨채소죽",
    "떡(꿀떡)",
    "떡(꿀설기)",
    "떡(증편)",
    "맑은무국",
    "맑은콩나물국",
    "메추리알장조림1,5,6",
    "무나물",
    "무생채",
    "미역국5,6",
    "바나나",
    "바나나머핀1,2,5,6",
    "바지락살국18",
    "방울토마토12",
    "배",
    "배추겉절이9",
    "배추김치9(백김치9)",
    "배추된장국5,6",
    "백김치9(배추김치9)",
    "백미밥",
    "보리밥",
    "북엇국1,5",
    "브로콜리무침",
    "사과",
    "새우볶음밥1,5,9",
    "쇠고기무국5,6,16",
    "쇠고기뭇국5,6,16",
    "쇠고기미역국5,6,16",
    "쇠고기채소죽16",
    "숙주나물",
    "순두부찌개5,10",
    "시금치나물5",
    "시래기된장국5,6",
    "시리얼2,6",
    "쌀과자",
    "애호박볶음",
    "어묵국1,5,6",
    "어묵탕1,5,6",
    "연근조림5,6",
    "열무나물",
    "오징어채소볶음5,6,17",
    "요구르트2",
    "우유2",
    "유부장국5,6",
    "유부초밥5,6",
    "제철과일 또는 감자스프2,6",
    "제철과일 또는 단호박죽",
    "제철과일 또는 닭죽15",
    "제철과일 또는 브로콜리감자스프2,6",
    "제철과일 또는 브로콜리죽",
    "제철과일 또는 잣죽19",
    "제철과일 또는 크림스프2,6",
    "짜장덮밥5,6,10",
    "찐감자",
    "찐고구마",
    "찐만두1,5,6,10,16",
    "찐옥수수",
    "차조밥",
    "청경채나물",
    "치즈빵1,2,5,6",
    "카레라이스2,5,6,10,12",
    "콩나물국",
    "콩나물무침5",
    "크래커2,6",
    "하이라이스2,5,6,10,12,16",
    "함박스테이크1,2,5,6,10,12,16",
    "현미밥",
    "호떡1,2,5,6",
    "흑미밥",
)


@pytest.mark.parametrize(
    "raw",
    [
        "총 511 kcal",
        "총 471 kcal",
        "총471kcal",
        "511kcal",
        "511 ㎉",
        "462/17",  # 열량/단백질 두 숫자
        "열량 511kcal / 단백질 17g",
        "에너지(kcal) 500",
        "단백질(g) 17.5",
        "열량",
        "총 511",
    ],
)
def test_열량_줄은_메뉴가_아니다(raw: str) -> None:
    assert is_nutrition_line(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        *_fixture_raws(),
        *DEMO_RAWS,
        "총각김치",  # "총" 이 들어 있어도 음식이다
        "철판볶음밥",
        "우유200ml",
        "3색나물",
        "백미밥1/2",
        # 번호만 읽힌 칸은 열량 줄이 아니다 — 이름을 못 읽은 칸으로 확인을 받는다 (photo_run)
        "2",
        "1,5",
        "5,6,15",
        "",
    ],
)
def test_음식_이름과_번호만_읽힌_칸은_열량_줄이_아니다(raw: str) -> None:
    assert is_nutrition_line(raw) is False


def test_정답셋_두_벌의_메뉴는_하나도_걸러지지_않는다() -> None:
    raws = [*_fixture_raws(), *DEMO_RAWS]

    assert len(_fixture_raws()) == 178
    assert [raw for raw in raws if is_nutrition_line(raw)] == []


# ── 급식표 저장 키 — 괄호 안 음식 이름도 키로 싣는다 (`meal_menu_keys`) ──
# `normalize_menu_key` 는 괄호 안을 통째로 지운다. "떡(꿀설기)" 의 키가 "떡" 만 되면 알레르기 ·
# 월령 판정이 "꿀" 을 못 본다. 메뉴 이름은 전부 지어낸 것이다.


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("떡(꿀설기)", ("떡", "꿀설기")),
        ("빵(우유카스텔라), 우유", ("빵", "우유카스텔라", "우유")),
        ("백김치(백깍두기)", ("백김치", "백깍두기")),  # 대체 메뉴
        ("생선(고등어)구이", (normalize_menu_key("생선(고등어)구이"), "고등어")),
        ("샐러드(사과, 배), 김치", ("샐러드", "사과", "배", "김치")),  # 괄호 안 쉼표에서 안 자른다
        ("과일(사과/배)", ("과일", "사과", "배")),
        ("과일(사과+배)", ("과일", "사과", "배")),
        # 원산지는 끝에 붙은 낱말만 뗀다 — 앞의 음식 이름은 남는다
        ("견과류(땅콩:국내산)", ("견과류", "땅콩")),
        ("견과류(땅콩 국내산)", ("견과류", "땅콩")),
        ("견과류(땅콩국내산)", ("견과류", "땅콩")),
        ("간식(호두·땅콩 국내산)", ("간식", "호두", "땅콩")),
        ("백미밥(쌀:국내산)", ("백미밥", "쌀")),
        ("조림(고등어 노르웨이산)", ("조림", "고등어")),
        ("간식(땅콩 세네갈산)", ("간식", "땅콩세네갈산")),  # 모르는 나라는 떼지 못해 통째로 남는다
        ("국(된장국 산적)", ("국", "된장국산적")),  # 가운데 "국산" 은 원산지가 아니다
        ("과일(사과 또는 배)", ("과일", "사과", "배")),
        ("과일(사과·배&귤)", ("과일", "사과", "배", "귤")),
        ("떡(꿀)", ("떡", "꿀")),  # 한 글자 음식은 남는다
        ("과자(잣, 밤, 콩, 팥, 깨, 게)", ("과자", "잣", "밤", "콩", "팥", "깨", "게")),
        ("두부조림[콩]", ("두부조림", "콩")),  # 대괄호도 같다
        ("떡（꿀설기）", ("떡", "꿀설기")),  # 전각 괄호
        ("(꿀설기)", ("꿀설기",)),  # 괄호 밖이 비어도 괄호 안은 남는다
        ("백김치(백김치)", ("백김치",)),  # 중복은 한 번
        ("떡(꿀설기), 떡(꿀설기)", ("떡", "꿀설기")),
        # 괄호 구간은 `normalize_menu_key` 와 같은 정규식이다 — 첫 구간이 "(감자(국내산)" 이고
        # 그 뒤 쉼표는 구간 밖이라 메뉴가 나뉜다
        ("수프(감자(국내산), 양파)", ("수프", "감자", "양파")),
        ("샐러드[사과, 배)땅콩]", ("샐러드", "사과", "배땅콩")),  # 섞인 괄호도 글자를 잃지 않는다
        # "원산지" 낱말과 끝의 원산지 낱말만 떼고 음식 이름은 남긴다
        ("볶음밥(원산지: 새우 수입산, 계란)", ("볶음밥", "새우", "계란")),
        ("카레(원산지:돼지고기-국내산)", ("카레", "돼지고기")),
        ("비빔밥(원산지: 쇠고기 호주산, 달걀 국내산)", ("비빔밥", "쇠고기", "달걀")),
        ("만두(원산지:새우 베트남산, 밀가루)", ("만두", "새우", "밀가루")),
        ("떡(원산지:국내산)", ("떡",)),
        ("밥(쌀:국내산, 원산지 표시)", ("밥", "쌀")),
    ],
)
def test_급식표_저장_키는_괄호_안_음식_이름을_더한다(title: str, expected: tuple[str, ...]) -> None:
    assert meal_menu_keys(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("닭고기(국내산)", ("닭고기",)),  # 원산지
        ("쇠고기(호주산)", ("쇠고기",)),
        ("돼지고기(국내산/미국산)", ("돼지고기",)),
        ("돼지고기(원산지 표시)", ("돼지고기",)),
        ("백미밥(1/2)", ("백미밥",)),  # 분량
        ("우유(200ml)", ("우유",)),
        ("귤(2개)", ("귤",)),
        ("밥(반공기)", ("밥",)),
        ("떡(소)", ("떡",)),  # 크기 · 표시 낱말
        ("국수(자율배식)", ("국수",)),
        ("찐(군)감자", (normalize_menu_key("찐(군)감자"),)),  # 조리법을 바꿔 적은 괄호
        ("삶은(구운)달걀", (normalize_menu_key("삶은(구운)달걀"),)),
        ("카레라이스(2.5.6)", ("카레라이스",)),  # 알레르기 번호
        ("죽(  )", ("죽",)),
    ],
)
def test_괄호_안_원산지_분량_표시는_키가_되지_않는다(title: str, expected: tuple[str, ...]) -> None:
    assert meal_menu_keys(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("떡(소고기)", ("떡", "소고기")),  # 낱말과 정확히 같을 때만 뺀다
        ("떡(대추)", ("떡", "대추")),
        ("전(알)", ("전", "알")),  # 숫자 없는 단위 한 글자는 애매하다 — 남긴다
    ],
)
def test_애매한_괄호_안은_남긴다(title: str, expected: tuple[str, ...]) -> None:
    assert meal_menu_keys(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "빵(우유카스텔라",
        "빵)우유(",
        "((떡))",
        "",
        ",,,",
        "(",
        ")",
        "[)",
        "떡(꿀, 우유",
        "(((",
        ")))",
    ],
)
def test_괄호가_어긋나도_예외_없이_키를_낸다(title: str) -> None:
    keys = meal_menu_keys(title)

    assert all(keys)
    assert len(keys) == len(set(keys))


def test_안_닫힌_괄호는_예전처럼_괄호_밖_키에_남는다() -> None:
    # 정규식에 안 걸리는 괄호는 아무 처리도 하지 않는다 — 글자를 잃지 않는다
    assert meal_menu_keys("빵(우유카스텔라") == ("빵우유카스텔라",)
    assert meal_menu_keys("빵(우유카스텔라, 우유") == ("빵우유카스텔라", "우유")
    assert meal_menu_keys("((떡))") == ("떡",)


def _old_meal_row_keys(title: str) -> tuple[str, ...]:
    """#908 전 `photo_run.meal_row` 의 식 그대로."""
    return tuple(k for k in (normalize_menu_key(n) for n in title.split(",")) if k)


@pytest.mark.parametrize(
    "title",
    [
        *(menu_display_name(raw) for raw in _fixture_raws() if "(" not in raw and "[" not in raw),
        *(menu_display_name(raw) for raw in DEMO_RAWS if "(" not in raw and "[" not in raw),
        "된장찌개, 백미밥1/2, 깍두기",
        "경기도과일 또는 고구마우유죽",
        "치킨가스&소스",
        "우유 2,5",
        ", 미역국,, ",
        "",
    ],
)
def test_괄호_없는_제목은_예전과_같은_키(title: str) -> None:
    assert meal_menu_keys(title) == _old_meal_row_keys(title)


# ── 성질 — 무작위 제목 (씨앗 고정) ──
# 거르기 낱말 · 단위 · "또는" · 원산지 낱말이 만들어질 수 없는 음절만 쓴다
_SAFE_SYLLABLES = "가나다라마꿀떡밥빵콩"
_FUZZ_MARKS = "()[],  "


def _random_title(rng: random.Random, alphabet: str, length: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, length)))


def test_무작위_제목에서_예전_키의_글자를_잃지_않는다() -> None:
    rng = random.Random(0)
    alphabet = _SAFE_SYLLABLES * 2 + _FUZZ_MARKS * 2
    for _ in range(3000):
        title = _random_title(rng, alphabet, 16)
        old = _old_meal_row_keys(title)
        new = meal_menu_keys(title)
        new_chars = set("".join(new))

        assert set("".join(old)) <= new_chars, title
        assert bool(new) or not old, title


def test_무작위_제목에서_예외_없이_빈_키_없는_튜플을_낸다() -> None:
    rng = random.Random(1)
    pieces = [
        *_SAFE_SYLLABLES, *"()[],/·&+ ", "（", "）", "［", "］", "，", "🍙", "★", "1", "2", "5",
        "또는", "원산지", "국내산", "호주산", ":", "-", "소", "반", "200ml",
    ]  # fmt: skip
    for _ in range(3000):
        title = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
        keys = meal_menu_keys(title)

        assert isinstance(keys, tuple)
        assert all(isinstance(key, str) and key for key in keys), title
        assert len(keys) == len(set(keys)), title
