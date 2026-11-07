"""발화에 적힌 체온 숫자 읽기. 표준 라이브러리만 쓴다.

Memory 의 체온 저장 대조와 응급 사전검사가 같은 읽기를 쓴다 — 한쪽만 바뀌면 저장은 막혔는데 응급
판정은 그 숫자를 보는 일이 생긴다.

섭씨 단위가 붙은 숫자만 센다. "38.5도" · "38도" · "38.5℃" · "38.5°C". 단위 없는 숫자는 날수 ·
월령 · 분일 수 있다("생후 38일" 이 38.0 으로 통과했다). "38도 5부" 는 38.5 하나다 — 38 은 아니다.
°F 는 받지 않는다(화씨 숫자를 섭씨 칸에 넣지 않는다).
"""

import re
from decimal import Decimal, InvalidOperation

_DEGREE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:℃|°(?!\s*[Ff])|도(?!\s*\d\s*부))")
_DEGREE_TENTH = re.compile(r"(?<![\d.])(\d+)\s*도\s*(\d)\s*부")


def temperatures_said(text: str) -> frozenset[Decimal]:
    """발화에 적힌 섭씨 체온들. 없으면 빈 집합."""
    found: set[Decimal] = set()
    for number in _DEGREE.findall(text):
        try:
            found.add(Decimal(number))
        except InvalidOperation:  # pragma: no cover — 정규식이 숫자만 뽑는다
            continue
    found.update(Decimal(f"{whole}.{tenth}") for whole, tenth in _DEGREE_TENTH.findall(text))
    return frozenset(found)
