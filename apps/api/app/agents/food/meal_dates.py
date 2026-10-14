"""급식 갱신 · 삭제의 날짜 표현 해석(`resolve_meal_date`). 모델이 날짜를 계산하지 않는다.

모델은 발화에서 날짜를 가리키는 구간만 골라 주고(예: "내일모레", "지난 화요일"), 어느 날인지는
여기서 코드가 정한다. 기준일(`today`)은 호출하는 쪽이 넘긴다(KST) — 여기서 `date.today()` 를
부르지 않는다.

풀 수 있는 표현은 `Food_Tool_명세.md` 의 표와 같다 — 오늘 · 어제 · 내일 · 모레 · 이번 주 / 다음 주
{요일} · 지난 / 저번 {요일} · {N}월 {N}일. 사전에 없으면 **추측하지 않는다**. 요일만 말한
"화요일" 은 지난 것인지 다가올 것인지 알 수 없어 풀지 않는다(되묻는다). 풀지 못하면
`DateParseError` 다.
"""

import re
from datetime import date, timedelta

from app.agents.common.datetime_rules import DateParseError, resolve_date

# 과거로 고칠 수 있는 범위. 그보다 오래된 급식은 보존 범위 밖이라 고칠 행이 없다
# (계획서 "전체에 걸린 값")
EDIT_PAST_DAYS = 30

_LAST_WEEKDAY = re.compile(r"^(?:지난|저번)\s*([월화수목금토일]요일)$")
_BARE_WEEKDAY = re.compile(r"^[월화수목금토일]요일$")


def resolve_meal_date(span: str, *, today: date) -> date:
    """날짜 구간을 확정 날짜로. 풀 수 없으면 `DateParseError`(되묻는다)."""
    text = span.strip()
    if not text:
        raise DateParseError("날짜 표현이 비어 있다.")
    key = re.sub(r"\s+", "", text)
    if key == "오늘자":
        return today
    if matched := _LAST_WEEKDAY.match(text):
        return resolve_date(matched.group(1), today=today, direction="past")
    if _BARE_WEEKDAY.match(key):
        raise DateParseError("요일만 말하면 어느 주인지 알 수 없다.")
    return resolve_date(text, today=today)


def in_edit_range(target: date, *, today: date) -> bool:
    """고칠 수 있는 날인가 — 과거 `EDIT_PAST_DAYS` 일까지와 미래 전부."""
    return target >= today - timedelta(days=EDIT_PAST_DAYS)
