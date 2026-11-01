"""응급 신호 판정 규칙. 순수 함수 — 사전(yaml)은 읽지 않는다. 표준 라이브러리만 쓴다.

보호자 발화에서 119 · 응급실 안내가 먼저 나가야 하는 말을 코드가 가른다. 모델에게 묻지 않는다
(루트 §2 "규칙(코드)이 막는다"). 표현 목록은 사전(`reference/emergency_signs.yaml`)이 정하고,
여기는 그 표현을 어떻게 맞추는지만 안다.

🚨 걸리지 않아야 할 것이 걸리면 도메인 Agent 가 전부 빠진다. 그래서 두 가지를 지킨다.
  - 표현마다 guard(오탐 취소)를 둘 수 있다 ("경련 얘기" · "숨 잘 쉬")
  - 월령은 **발화에 적힌 것만** 읽는다. 기간 표현("두 달 전부터")은 월령이 아니다
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.rules.temperature import temperatures_said
from app.rules.term_match import Term, match_terms


@dataclass(frozen=True)
class Sign:
    """응급 신호 하나. `all_of` 의 Term 이 전부 걸려야 한다.

    `infant=True` 면 발화에 적힌 월령이 기준 미만이어야 한다. 그리고 `all_of` 대신 기준 이상의
    체온을 말한 것으로도 걸린다("두 달 된 아기가 열이 38도야" 에는 열 표현이 없어도 체온이 있다).
    """

    key: str
    all_of: tuple[Term, ...]
    infant: bool = False


@dataclass(frozen=True)
class InfantThreshold:
    below_month: int
    fever_temperature: Decimal


# 한글 수사는 앞에 한글이 붙어 있지 않을 때만 읽는다. "열두 달" 의 "두 달", "스물한 달" 의
# "한 달" 이 월령 2 · 1 로 읽히면 12개월 · 21개월 아이에게도 응급 안내가 나간다
_NUMBER = r"(?P<n>\d+|(?<![가-힣])(?:열한|열|아홉|여덟|일곱|여섯|다섯|네|세|두|한))"
_UNIT = r"(?P<unit>개월|달|주|일)"
# 월령으로 읽는 꼴은 이 둘뿐이다. "두 달 전부터" · "3개월째" · "두 달 동안" 은 기간이라 안 걸린다
_AFTER_BIRTH = re.compile(rf"생후\s*{_NUMBER}\s*{_UNIT}")
# "아이" 는 아이스크림 · 아이돌 같은 낱말의 앞 글자이기도 하다
_AGED = re.compile(
    rf"{_NUMBER}\s*{_UNIT}\s*(?:된|짜리|차\b|아기|애기|아이(?!스|돌|디|템|콘|패드|폰|라인))"
)
_NEWBORN = "신생아"
# "열난 지 3일 된" · "다닌 지 한 달 된 아이" 의 "N일 된" 은 아이 월령이 아니라 지난 기간이다.
# 월령으로 읽으면 3살 아이에게도 응급 안내가 나간다
_ELAPSED = re.compile(r"지\s*$")
# 3개월 미만의 체온은 열 이야기일 때만 센다. "목욕물 38도" 는 아이 체온이 아니다
# "열" 한 글자는 "열심히" · "열쇠" 에도 있다. 열 이야기로 읽히는 꼴만 센다. 부정("열 안 나")은
# 열이 없다는 말이라 체온 경로를 열지 않는다
_FEVER_CUE = re.compile(r"체온|발열|고열|열\s*(?:이|은|을|도|재|나|났|오|올|있|높|\d)")
_FEVER_NEGATION = re.compile(r"열(?:이|은)?\s*(?:안|없|나지\s*않)")
_WORD_NUMBERS = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
    "열한": 11,
}
_DAYS_PER_MONTH = 30


def stated_months(text: str) -> tuple[int, ...]:
    """발화에 적힌 아이 월령들(개월). 주 · 일은 30일을 1개월로 내려 센다. 없으면 빈 튜플."""
    found: list[int] = []
    for pattern in (_AFTER_BIRTH, _AGED):
        for match in pattern.finditer(text):
            if pattern is _AGED and _ELAPSED.search(text[: match.start()]):
                continue
            found.append(_months(match["n"], match["unit"]))
    if _NEWBORN in text:
        found.append(0)
    return tuple(dict.fromkeys(found))


def _months(number: str, unit: str) -> int:
    value = int(number) if number.isdigit() else _WORD_NUMBERS[number]
    if unit in ("개월", "달"):
        return value
    days = value * 7 if unit == "주" else value
    return days // _DAYS_PER_MONTH


def detect(text: str, signs: Sequence[Sign], infant: InfantThreshold) -> tuple[str, ...]:
    """걸린 `Sign.key` 를 `signs` 순서로. 걸린 것이 없으면 빈 튜플."""
    months = stated_months(text)
    return tuple(sign.key for sign in signs if _fires(text, sign, months, infant))


def _fires(text: str, sign: Sign, months: tuple[int, ...], infant: InfantThreshold) -> bool:
    # all_of 가 비면 모든 말에 걸린다 — 사전 검사가 막지만 규칙도 스스로 막는다
    matched = bool(sign.all_of) and all(match_terms(text, (term,)) for term in sign.all_of)
    if not sign.infant:
        return matched
    # 한 아이의 지금 월령은 하나다. "생후 2개월 때 접종했는데 지금 7개월" 은 가장 큰 값을 본다
    if not months or max(months) >= infant.below_month:
        return False
    hot = (
        any(value >= infant.fever_temperature for value in temperatures_said(text))
        and _FEVER_CUE.search(text) is not None
        and _FEVER_NEGATION.search(text) is None
    )
    return matched or hot
