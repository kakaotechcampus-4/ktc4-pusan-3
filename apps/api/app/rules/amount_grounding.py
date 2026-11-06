"""모델이 지어낸 시간 · 횟수 · 나이 수치를 가려낸다 — 보호자가 말한 수치만 근거가 있다.

문서 행이 없는 요청 기반 추천("유튜브는 하루 1시간까지")은 모델이 쓴 수치가 그대로 지침처럼
읽힌다. 근거가 없는 수치는 그 후보를 빼는 이유다. 보호자가 같은 수치를 말했으면 근거가 있다.

    수치      숫자(아라비아 · 한 · 두 · 세 · 네 · 다섯 ~ 열 · 반) + 단위(`AMOUNT_UNITS`)
    같은 수치  값과 단위가 같다. 한글 숫자와 아라비아 숫자는 같은 값이다 ("세 번" = "3번")
    안 거는 것  단위가 없는 숫자 ("1부터 10까지 세기" · "블록 3개")
    `units=None`  단위와 상관없이 숫자 전부 — 문장의 숫자가 단 행 · 질문에 있는 것뿐인지 볼 때

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re
from collections.abc import Iterator, Sequence
from functools import cache

AMOUNT_UNITS: tuple[str, ...] = ("분", "시간", "초", "번", "회", "살", "세", "개월", "주", "일")

_KOREAN_NUMBERS: dict[str, float] = {
    "한": 1,
    "하나": 1,
    "두": 2,
    "둘": 2,
    "세": 3,
    "셋": 3,
    "네": 4,
    "넷": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
    "반": 0.5,
}
# 단위 글자가 낱말의 앞머리일 때("1세트" · "3초등학생")는 수치가 아니다
_COMPOUNDS: dict[str, str] = {
    "세": "트|상|계|대|금",
    "초": "등|록|점|콜",
    "일": "정|상|과|어|찍",
    "주": "변|위|인|제|의|말",
    "회": "사|의|원|복|전",
    "번": "호|역",
    "분": "위|야|량|명",
}
_DIGITS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)")
_KOREAN = "|".join(sorted(_KOREAN_NUMBERS, key=len, reverse=True))


@cache
def _with_unit(units: tuple[str, ...]) -> re.Pattern[str]:
    parts = [
        re.escape(unit) + (f"(?!{_COMPOUNDS[unit]})" if unit in _COMPOUNDS else "")
        for unit in sorted(units, key=len, reverse=True)
    ]
    number = rf"(?:(?<![\d.])\d+(?:\.\d+)?|(?<![가-힣])(?:{_KOREAN}))"
    return re.compile(rf"(?P<num>{number})\s*(?P<unit>{'|'.join(parts)})")


def _value(raw: str) -> float:
    return _KOREAN_NUMBERS[raw] if raw in _KOREAN_NUMBERS else float(raw)


def _amounts(text: str, units: Sequence[str] | None) -> Iterator[tuple[float, str | None]]:
    """글에서 (값, 단위)를 나타난 순서대로. `units` 가 None 이면 단위는 항상 None 이다."""
    if units is None:
        found = [(m.start(), _value(m.group(1)), None) for m in _DIGITS.finditer(text)]
        found += [
            (m.start(), _value(m.group("num")), None)
            for m in _with_unit(AMOUNT_UNITS).finditer(text)
            if m.group("num") in _KOREAN_NUMBERS
        ]
        for _, value, unit in sorted(found, key=lambda item: item[0]):
            yield value, unit
        return
    for match in _with_unit(tuple(units)).finditer(text):
        yield _value(match.group("num")), match.group("unit")


def _label(value: float, unit: str | None) -> str:
    return f"{value:g}{unit or ''}"


def ungrounded_amounts(
    texts: Sequence[str],
    grounds: Sequence[str],
    *,
    units: Sequence[str] | None = AMOUNT_UNITS,
) -> tuple[str, ...]:
    """`texts` 의 수치 중 `grounds` 에 같은 수치가 없는 것. 돌려주는 것은 정규화한 표기다.

    표기("30분" · "0.5시간")는 판정용이다. 후보 원문을 로그에 남기지 않는 규칙대로 로그에
    싣지 않고 걸렸는지만 쓴다. 같은 수치는 한 번만 돌려준다.
    """
    grounded = {amount for text in grounds for amount in _amounts(text, units)}
    out: list[str] = []
    for text in texts:
        for amount in _amounts(text, units):
            if amount not in grounded and (label := _label(*amount)) not in out:
                out.append(label)
    return tuple(out)
