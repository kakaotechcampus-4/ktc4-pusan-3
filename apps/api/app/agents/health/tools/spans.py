"""모델이 복사한 span 이 원문에 있는지 본다.

복약 라벨에서 모델은 약 이름 · 용량 · 시점 표현 · 기간 · 이유를 **원문 그대로 복사**만 하고, 복사한
말이 원문의 부분 문자열인지는 코드가 본다. 틀린 span 으로는 초안을 만들지 않는다 — 약 이름이나
용량이 미세하게 바뀐 채 화면에 나가는 것이 이 검사가 막는 일이다.

공백은 정규화해서 본다(여러 칸 · 줄바꿈을 한 칸으로). 그 밖의 보정은 없다 — 대소문자 · 단위 환산 ·
띄어쓰기 지우기를 하면 다른 말이 같은 말로 통과한다.

부분 문자열이라도 **숫자 · 영문 · 소수점 한가운데에서 자른 말은 아니다**. "0.5ml" 에서 "5ml" 을
꺼내면 용량이 열 배가 되고 투약의뢰서로 어린이집에 간다. 앞뒤가 숫자 · 영문이거나("15ml" ·
"5ml/kg" · "항생제A"), 앞이 소수점("0.5ml") · 뒤가 소수점 숫자("5.5ml")이면 그 자리의 일치는
세지 않는다.
한글 조사("5ml씩") · 쉼표 · 문장 끝 마침표("5ml.")는 경계다.
"""

import re
from collections.abc import Sequence

_NOT_BEFORE = r"(?<![0-9A-Za-z])(?<![0-9]\.)"
_NOT_AFTER = r"(?![0-9A-Za-z/])(?!\.[0-9])"


def normalize_spaces(text: str) -> str:
    return " ".join(text.split())


def is_verbatim(span: str, source_texts: Sequence[str]) -> bool:
    """`span` 이 `source_texts` 중 하나의 부분 문자열인가(공백 정규화 후). 빈 span 은 아니다."""
    needle = normalize_spaces(span)
    if not needle:
        return False
    pattern = re.compile(_NOT_BEFORE + re.escape(needle) + _NOT_AFTER)
    return any(pattern.search(normalize_spaces(text)) for text in source_texts)
