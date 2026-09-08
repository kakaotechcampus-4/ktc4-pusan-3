"""급식표 메뉴 원문에서 알레르기 번호를 뽑는 규칙.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re
from dataclasses import dataclass

# 식품위생법 표시 대상 19종. 이 dict 의 키가 "범위 안"의 유일한 정본이다.
ALLERGEN_NAMES: dict[int, str] = {
    1: "난류",
    2: "우유",
    3: "메밀",
    4: "땅콩",
    5: "대두",
    6: "밀",
    7: "고등어",
    8: "게",
    9: "새우",
    10: "돼지고기",
    11: "복숭아",
    12: "토마토",
    13: "아황산류",
    14: "호두",
    15: "닭고기",
    16: "쇠고기",
    17: "오징어",
    18: "조개류",
    19: "잣",
}

# 원문자 ① ~ ⑳ (U+2460 ~ U+2473) → 1 ~ 20
CIRCLED: dict[str, int] = {chr(0x2460 + i): i + 1 for i in range(20)}

# 반각 ( ) · 전각 （ ） 모두 허용.
# 닫는 괄호가 없으면(OCR 에서 흔한 오류) 문자열 끝까지를 괄호 안으로 본다.
_PAREN = re.compile(r"[(（]([^)）]*)(?:[)）]|$)")
_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class ParsedAllergens:
    """parse_allergens 의 결과. 두 필드 모두 오름차순 · 중복 제거.

    codes   — 19종 범위 안. 그대로 allergen_codes 로 저장해도 된다.
    unknown — 19종 범위 밖. 비어 있지 않으면 사람 검수 플래그 (버리지 않는다).
    """

    codes: tuple[int, ...]
    unknown: tuple[int, ...]


def parse_allergens(raw: str) -> ParsedAllergens:
    """메뉴 원문에서 알레르기 번호만 추출. LLM 개입 없음.

    - 괄호 안 숫자: `(1,5,6,16)` `(1.5.6.16)` `（1, 5）` — 숫자 사이는 무엇이든 구분자로 본다.
    - 원문자: `①②⑤⑬` — 괄호 유무와 무관하게 문자열 전체에서 찾는다.
    - 괄호 밖의 숫자(`3색나물`)와 괄호 안의 글자(`(중)`)는 무시한다.
    """
    found: set[int] = set()
    for inner in _PAREN.findall(raw):
        found.update(int(n) for n in _NUMBER.findall(inner))
    found.update(CIRCLED[ch] for ch in raw if ch in CIRCLED)
    codes = tuple(sorted(n for n in found if n in ALLERGEN_NAMES))
    unknown = tuple(sorted(n for n in found if n not in ALLERGEN_NAMES))
    return ParsedAllergens(codes=codes, unknown=unknown)
