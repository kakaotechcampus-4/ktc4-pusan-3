"""메뉴명을 `menu_key` 로 바꾸는 규칙. 정본은 `food_agent_own_table.md` §1.

급식표 · 보호자 말 · 영양DB 의 이름이 같은 키로 만나게 하는 것이 전부다. 공백 · 괄호 · 급식표
알레르기 번호를 지우고 글자를 맞춘다. 비슷한 이름을 합치지 않는다 — 된장찌개와 된장찌개 두부는
다른 키다 (알레르기를 놓치는 쪽으로 틀리기 때문이다, 외부연결_계획 §2).

번호를 떼는 것은 읽는 것이 아니라 지우는 것이다. 기관이 아이의 알레르기를 관리하므로 급식표의
번호는 판정에 쓰지 않는다. 떼지 않으면 "카레라이스(2.5.6)" 가 카탈로그에서 안 찾아져 영양 계산에서
빠진다.

급식표 저장 키(`meal_menu_keys`)만 괄호 안 음식 이름을 키로 하나 더 싣는다. "떡(꿀설기)" 의 키가
"떡" 하나면 알레르기 · 월령 판정이 "꿀" 을 못 본다. `normalize_menu_key` 는 바꾸지 않는다 — Food
Agent 여러 곳이 같은 함수로 급식표 · 보호자 말 · 영양DB 의 키를 맞추므로, 바꾸면 이미 맞던 키가
어긋난다. 결정은 meal-plan-pipeline-v1 결정 ⑤.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re
import unicodedata

from app.rules.allergen import strip_allergen_marks
from app.rules.term_match import normalize

_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")

# 메뉴 이름에 남기는 기호. 글자 · 숫자 · 공백 말고는 이것만 남기고
# 나머지(★ · ` · : · 따옴표 등)는 지운다.
# `,` 는 한 칸에 두 메뉴가 붙어 온 경우 저장 키를 나누는 자리다 (photo_run.meal_row)
_NAME_KEEP = frozenset("()&/,·~-%+. ")
_NAME_EDGE = " ,.·&/-~+"
_REPEATED_AMP = re.compile(r"&{2,}")
_EMPTY_PARENS = re.compile(r"\(\s*\)")
_SPACES = re.compile(r"\s+")


# 열량 · 영양 표기 줄의 낱말. 이것과 숫자 · 기호만 있는 원문은 메뉴가 아니다 (`is_nutrition_line`)
_NUTRITION_WORDS = re.compile(r"열량|에너지|칼로리|단백질|탄수화물|지방|당류|나트륨|칼슘|총")
_NUTRITION_UNITS = re.compile(r"(?<![a-z])(?:kcal|cal|mg|g)(?![a-z])")
_NUMBER = re.compile(r"\d+")
# 알레르기 번호는 1~22 다. 세 자리 숫자는 번호가 아니라 열량이다 ("462/17")
_ENERGY_DIGITS = 100


def is_nutrition_line(raw: str) -> bool:
    """급식표 원문 한 항목이 열량 · 영양 표기 줄인가 ("총 511 kcal" · "462/17").

    🚨 보수적으로 판정한다 — 음식 이름을 잘못 버리면 영양 계산 · 메뉴 기억에서 빠진다.
       열량 · 영양 낱말 · 단위와 숫자 · 기호를 지우고 **글자가 하나도 남지 않을 때만** 참이다.
       "총각김치" 는 "각김치" 가 남아 메뉴다. 번호만 있는 원문("2" · "5,6")은 열량 줄이 아니라
       이름을 못 읽은 칸이다 — 세 자리 숫자도, 열량 낱말도 없으면 거짓이다.
    """
    text = unicodedata.normalize("NFKC", raw).lower()
    marked = _NUTRITION_UNITS.sub(" ", _NUTRITION_WORDS.sub(" ", text))
    if any(ch.isalpha() for ch in marked):
        return False
    has_word = marked != text
    has_energy = any(int(n) >= _ENERGY_DIGITS for n in _NUMBER.findall(marked))
    return has_word or has_energy


def normalize_menu_key(name: str) -> str:
    """메뉴명 → `menu_key`. 이름이 남지 않으면 빈 문자열이다.

    NFKC 로 전각 괄호 · 영문을 맞추고, 괄호 안(양 · 원산지 · 번호)을 지우고, 괄호 밖에 붙은
    알레르기 번호("우유 2,5" · "우유②")를 지운 뒤, 공백 · 문장부호를 빼고 소문자로 맞춘다.
    """
    text = unicodedata.normalize("NFKC", name)
    text = _BRACKETS.sub(" ", text)
    return normalize(strip_allergen_marks(text))


def menu_display_name(raw: str) -> str:
    """급식표 원문 한 항목 → 화면 제목 · 저장 키에 쓰는 메뉴 이름 (#908).

    모델이 낸 이름이 아니라 원문에서 만든다 — 모델 이름 끝에 " Riot" · "/Optional?" 같은 찌꺼기가
    붙어 화면과 저장 키까지 간 적이 있다. 원문의 글자를 지우기만 하고 새 글자를 만들지 않는다.

    알레르기 번호를 떼고(`strip_allergen_marks`), 꾸밈 기호를 지우고, 겹친 `&` 는 하나로, 번호만
    있던 빈 괄호는 지우고, 칸 끝 쉼표 · 공백을 뗀다. 대체 메뉴 괄호("백김치(백깍두기)")와
    "또는" · 분량("1/2")은 남긴다 — 모델 이름과 같은 모양이다.
    """
    stripped = strip_allergen_marks(raw)
    text = "".join(ch for ch in stripped if ch.isalnum() or ch in _NAME_KEEP)
    text = _REPEATED_AMP.sub("&", text)
    text = _EMPTY_PARENS.sub("", text)
    text = _SPACES.sub(" ", text)
    return text.strip(_NAME_EDGE)


# ── 급식표 저장 키 — 괄호 안 음식 이름까지 (#908) ──

# 괄호 안에서 음식 이름을 나누는 자리
_INNER_SPLIT = re.compile(r"[,/·&+]|또는")
# 원산지 — "원산지" 낱말을 지우고, 키 **끝에** 붙은 아는 원산지 낱말만 뗀다.
# "땅콩국내산" → "땅콩", "원산지새우수입산" → "새우", "국내산" → 버림.
# 🚨 가운데는 건드리지 않는다 — "된장국산적" 의 "국산" 은 원산지가 아니다.
# 모르는 나라("땅콩세네갈산")는 떼지 못해 통째로 남는다 — 판정이 이름 속 "땅콩" 을 본다
_ORIGIN_WORD = "원산지"
_ORIGIN_PLACES = (
    "국내", "국", "수입", "외국", "원양", "연근해", "중국", "미국", "호주", "러시아", "베트남",
    "태국", "칠레", "페루", "캐나다", "브라질", "노르웨이", "뉴질랜드", "인도", "필리핀", "멕시코",
    "스페인", "아르헨티나",
)  # fmt: skip
# 긴 낱말이 먼저 맞게 한다 ("미국산" 이 "국산" 보다 먼저). 끝에서 여러 번 이어져도 다 뗀다
_ORIGIN_TAIL = re.compile("(?:" + "|".join(sorted(_ORIGIN_PLACES, key=len, reverse=True)) + ")산$")
_LETTER = re.compile(r"[가-힣a-z]")
# 분량 — 숫자와 단위만. normalize 뒤라 "1/2" 는 "12", "200 ml" 은 "200ml" 이다
_AMOUNT = re.compile(
    r"(?:\d+|g|kg|ml|l|cc|개|쪽|장|조각|알|컵|공기|인분|큰술|작은술|스푼|그릇|접시|봉|팩|줄|마리|반)+"
)
_AMOUNT_MARK = re.compile(r"\d|반")
# 크기 · 표시 낱말. 이것과 정확히 같을 때만 뺀다 ("소고기" 는 남는다).
# 군 · 구운 · 찐 · 삶은 은 조리법을 바꿔 적은 괄호다 ("찐(군)감자" · "삶은(구운)달걀")
_MARK_WORDS = frozenset(
    {
        "소", "중", "대", "특", "선택", "자율", "자율배식", "대체", "대체식", "행사", "행사식",
        "친환경", "무농약", "유기농", "무항생제", "완제품", "냉동", "수제", "추가", "제공", "제외",
        "미제공", "군", "구운", "찐", "삶은", "표시", "표기", "참조",
    }
)  # fmt: skip


def meal_menu_keys(title: str) -> tuple[str, ...]:
    """급식 항목 제목(메뉴 이름을 ", " 로 이은 글) → `daycare_meal.menu_keys`.

    괄호 구간은 `normalize_menu_key` 가 지우는 구간과 **같은 정규식**(`_BRACKETS`)으로 정한다.
    그래서 괄호 밖 키에서 지워진 글자는 괄호 안 조각으로 간다 — 거른 조각(원산지 · 분량 · 표시
    낱말) 말고는 어느 키에도 안 남는 글자가 없다.
    안 닫힌 괄호 · 짝 없는 닫는 괄호는 정규식에 안 걸려 예전처럼 괄호 밖 키에 남는다.

    구간 밖 쉼표에서만 메뉴를 나눈다 — "샐러드(사과, 배)" 는 한 메뉴다. 메뉴마다 괄호 밖 키
    (`normalize_menu_key(메뉴)`)를 먼저 싣고, 구간의 안쪽을 `,` `/` `·` `&` `+` "또는" 에서 나눠
    음식 이름을 키로 더한다. 순서를 지키고 겹친 키는 한 번만 싣는다.

    괄호 없는 제목의 키는 예전(`title.split(",")`)과 같다. 다만 겹친 키는 한 번만 싣고, NFKC 를
    먼저 하므로 전각 쉼표("，")도 메뉴를 나눈다.

    `normalize_menu_key` 를 바꾸지 않고 여기서만 더한다. 괄호 밖 키는 그대로라 카탈로그 · 영양
    계산이 찾던 키가 바뀌지 않고, 괄호 안 키는 판정이 이름으로 한 번 더 본다(카탈로그에 없으면
    "확인 못 함").

    원산지는 "원산지" 낱말과 조각 끝에 붙은 원산지 낱말("땅콩국내산" 의 "국내산")만 떼고 음식
    이름은 남긴다. "원산지: 새우 수입산" → "새우".

    🚨 괄호 안에서 빼는 것은 음식이 아닌 것이 분명한 조각뿐이다 — 원산지만인 것("국내산"),
       분량("1/2" · "200ml" · "반공기"), 크기 · 표시 낱말과 정확히 같은 것("소" · "자율배식").
       애매하면 남긴다. 모르는 나라("땅콩세네갈산")는 떼지 못해 통째로 남는다.
       남기면 "확인 못 함" 으로 한 번 더 보게 되고, 지우면 알레르기를 놓친다. "꿀" · "잣" 같은
       한 글자 음식, 숫자 없는 단위 한 글자("알")도 남는다.

    보호자가 화면에서 제목을 고칠 수 있어 괄호가 어긋나도 예외를 내지 않는다.
    """
    text = unicodedata.normalize("NFKC", title)
    spans = [match.span() for match in _BRACKETS.finditer(text)]
    keys: list[str] = []
    next_span = 0  # 구간 · 메뉴 모두 앞에서부터 차례다. 구간은 쉼표를 넘지 않아 한 메뉴 안에 든다
    for menu_start, menu_end in _menu_ranges(text, spans):
        keys.append(normalize_menu_key(text[menu_start:menu_end]))
        while next_span < len(spans) and spans[next_span][1] <= menu_end:
            span_start, span_end = spans[next_span]
            next_span += 1
            inner = text[span_start + 1 : span_end - 1]  # 양 끝 괄호 글자 하나씩만 뗀다
            keys.extend(
                key
                for key in (_food_key(normalize_menu_key(p)) for p in _INNER_SPLIT.split(inner))
                if key
            )
    return tuple(dict.fromkeys(key for key in keys if key))


def _menu_ranges(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """어느 괄호 구간에도 들지 않은 쉼표에서 나눈 메뉴들의 (시작, 끝)."""
    ranges: list[tuple[int, int]] = []
    start = 0
    position = 0
    for span_start, span_end in [*spans, (len(text), len(text))]:
        for i in range(position, span_start):  # 구간 사이의 글만 본다
            if text[i] == ",":
                ranges.append((start, i))
                start = i + 1
        position = span_end
    ranges.append((start, len(text)))
    return ranges


def _food_key(key: str) -> str | None:
    """괄호 안 조각(정규화한 키) → 저장할 키. 음식이 아닌 것이 분명하면 None.

    "원산지" 낱말을 지우고, 끝에 붙은 원산지 낱말을 떼고 남은 것을 본다. 남은 것이 비거나
    글자가 없거나 분량 · 표시 낱말이면 None 이다.
    """
    key = key.replace(_ORIGIN_WORD, "")
    while (stripped := _ORIGIN_TAIL.sub("", key)) != key:
        key = stripped
    if not key or not _LETTER.search(key):
        return None
    if _AMOUNT.fullmatch(key) and _AMOUNT_MARK.search(key):
        return None
    return None if key in _MARK_WORDS else key
