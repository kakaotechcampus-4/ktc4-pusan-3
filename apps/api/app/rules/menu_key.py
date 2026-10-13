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


def normalize_menu_key(name: str) -> str:
    """메뉴명 → `menu_key`. 이름이 남지 않으면 빈 문자열이다.

    NFKC 로 전각 괄호 · 영문을 맞추고, 괄호 안(양 · 원산지 · 번호)을 지우고, 괄호 밖에 붙은
    알레르기 번호("우유 2,5" · "우유②")를 지운 뒤, 공백 · 문장부호를 빼고 소문자로 맞춘다.
    """
    text = unicodedata.normalize("NFKC", name)
    text = _BRACKETS.sub(" ", text)
    return normalize(strip_allergen_marks(text))
