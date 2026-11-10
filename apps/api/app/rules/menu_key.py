"""메뉴명을 `menu_key` 로 바꾸는 규칙. 정본은 `food_agent_own_table.md` §1.

급식표 · 보호자 말 · 영양DB 의 이름이 같은 키로 만나게 하는 것이 전부다. 공백 · 괄호 · 급식표
알레르기 번호를 지우고 글자를 맞춘다. 비슷한 이름을 합치지 않는다 — 된장찌개와 된장찌개 두부는
다른 키다 (알레르기를 놓치는 쪽으로 틀리기 때문이다, 외부연결_계획 §2).

번호를 떼는 것은 읽는 것이 아니라 지우는 것이다. 기관이 아이의 알레르기를 관리하므로 급식표의
번호는 판정에 쓰지 않는다. 떼지 않으면 "카레라이스(2.5.6)" 가 카탈로그에서 안 찾아져 영양 계산에서
빠진다.

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
