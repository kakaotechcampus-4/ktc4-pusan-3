"""아이의 월령과 단계를 계산하는 규칙.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

네 도메인 Agent 가 같은 함수를 쓴다. 공통이 정하는 것은 **월령 계산까지**이고,
그 값으로 tool 을 어떻게 가를지는 도메인마다 다르다 (docs/agents/shared/Tool_공통.md §2).

    Food     stage 를 배타적 범주로 — 수유기와 유아식기는 먹을 수 있는 것이 질적으로 다르다
    Growth   tool 별 min_month 눈금 — 열리는 시점만 다르고 배타적인 tool 이 없다
    Health   가르지 않는다 — 검진 차수·접종 시기라는 계산 방식만 바뀐다
    Activity 미정

stage 는 참고값이지 공통 게이팅 축이 아니다.

🚨 월령 경계값은 이 파일에 없다. `reference/age_gates.yaml` 이 정본이고, 표준 라이브러리만 쓰는 이
층은 YAML 을 읽을 수 없어서 경계를 인자로 받는다. 파일에서 읽어 넘기는 것은
`app/agents/common/age.py` 다 — Agent 는 거기 있는 같은 이름의 함수를 쓴다 (#372).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Stage = Literal["infant_milk", "infant_weaning", "toddler", "preschool"]
Band = Literal["infant", "toddler"]

# 단계 경계 — (시작 월령, 단계) 를 월령 오름차순으로. 월령이 시작 월령 이상인 마지막 칸이 그
# 아이의 stage 다. 첫 칸은 0 에서 시작해야 한다 (로더가 검사한다).
# 값은 `reference/age_gates.yaml` 이 정한다.
StageBoundaries = tuple[tuple[int, Stage], ...]

# 화면에 내리는 나이 문구의 경계. 월령이 이 값보다 작으면 그 단위로 부른다.
#   _DAYS_BEFORE_MONTHS 미만   → "생후 N일"
#   _MONTHS_BEFORE_YEARS 미만  → "N개월"
#   그 뒤                       → "만 N세"
# 부르는 단위는 정해진 표준이 없어 잠정값이다 (#198). 바꾸면 이 두 숫자만 고친다.
_DAYS_BEFORE_MONTHS = 3
_MONTHS_BEFORE_YEARS = 24

_BANDS: dict[Stage, Band] = {
    "infant_milk": "infant",
    "infant_weaning": "infant",
    "toddler": "toddler",
    "preschool": "toddler",
}


@dataclass(frozen=True)
class LifeStage:
    """게이팅에 쓰는 아이의 연령 값. `stage` 는 출생 후 월령 기준."""

    months: int
    stage: Stage
    big: Band


def life_stage(birth_date: date, today: date, boundaries: StageBoundaries) -> LifeStage:
    """생년월일과 기준일로 월령·단계를 계산한다.

    today 는 호출하는 쪽이 넘긴다. 이 함수 안에서 date.today() 를 부르지 않는다 —
    UTC 서버에서 부르면 KST 00:00~09:00 사이에 하루가 어긋나 그 시간대에만 게이트가 안 열린다.
    """
    months = months_between(birth_date, today)
    stage = stage_of(months, boundaries)
    return LifeStage(months=months, stage=stage, big=_BANDS[stage])


def months_between(birth_date: date, today: date) -> int:
    """민법 기준 달력 계산으로 개월 수를 센다.

    (today - birth).days // 30 을 쓰면 안 된다. 30일로 나누면 6년이면 두 달 앞서 열린다.

    말일 경계는 민법 §160③ 을 따른다 — 해당일이 없으면 그 달의 말일이 그날이다.
    1월 31일생은 평년 2월 28일에 1개월이 된다.

    생일 당일 전환이다. 12개월이 되는 날 그 월령이 된다.
    """
    _require_plain_date(birth_date, "birth_date")
    _require_plain_date(today, "today")
    if today < birth_date:
        raise ValueError(f"기준일이 생년월일보다 앞선다: birth_date={birth_date} today={today}")

    months = (today.year - birth_date.year) * 12 + (today.month - birth_date.month)
    if today.day < birth_date.day and not _is_month_end_anniversary(birth_date, today):
        months -= 1
    return months


def date_at_month(birth_date: date, months: int) -> date:
    """기준 월령이 되는 날. 해당 일이 없는 달은 말일로 맞춘다."""
    _require_plain_date(birth_date, "birth_date")
    if months < 0:
        raise ValueError(f"월령은 음수일 수 없다: {months}")
    year, month_index = divmod(birth_date.year * 12 + birth_date.month - 1 + months, 12)
    month = month_index + 1
    return date(year, month, min(birth_date.day, _last_day_of_month(year, month)))


def age_display(birth_date: date, today: date) -> str:
    """화면에 그대로 보여 줄 나이 문구. 프론트는 나이를 계산하지 않고 이 값을 쓴다.

    today 는 KST 날짜를 넘긴다 — 이유는 life_stage() 와 같다.
    "생후 N일" 은 태어난 날을 1일로 센다 — 백일과 같은 기념일식이고, 국내 육아 앱
    (BabyTime · 베베스냅 · 조이로그)도 이렇게 센다. 의료 · 영유아 검진의 "생후 N일" 은
    지난 날 수(태어난 날 0일)라 같은 날이 하루 다르게 불린다 (#198 에서 물을 것).
    """
    months = months_between(birth_date, today)
    if months < _DAYS_BEFORE_MONTHS:
        return f"생후 {(today - birth_date).days + 1}일"
    if months < _MONTHS_BEFORE_YEARS:
        return f"{months}개월"
    return f"만 {months // 12}세"


def stage_of(months: int, boundaries: StageBoundaries) -> Stage:
    """월령을 단계로 바꾼다. 경계 월령이 되는 날 다음 단계가 된다."""
    if months < 0:
        raise ValueError(f"월령은 음수일 수 없다: {months}")
    for boundary, stage in reversed(boundaries):
        if months >= boundary:
            return stage
    raise AssertionError("boundaries 의 첫 칸이 0 이 아니다")  # pragma: no cover


def first_month_of(stage: Stage, boundaries: StageBoundaries) -> int:
    """그 단계가 시작하는 월령. 월령을 모르고 단계만 알 때 가장 어린 쪽으로 볼 때 쓴다."""
    for boundary, candidate in boundaries:
        if candidate == stage:
            return boundary
    raise ValueError(f"모르는 단계: {stage!r}")


def _is_month_end_anniversary(birth_date: date, today: date) -> bool:
    """생일이 이 달에는 없어서 말일이 그날이 되는 경우.

    31일생의 2월처럼 해당일이 아예 없을 때만 참이다. 27일이 28일보다 작다고 걸리면 안 되므로
    "오늘이 이 달의 말일" 과 "생일이 이 달에는 없음" 을 함께 본다.
    """
    last_day = _last_day_of_month(today.year, today.month)
    return today.day == last_day and birth_date.day > last_day


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _require_plain_date(value: date, name: str) -> None:
    """datetime 이 date 자리에 들어오는 것을 막는다.

    datetime 은 date 의 하위 클래스라 isinstance 로는 걸러지지 않는다. 그대로 두면
    timezone 이 붙은 값이 날짜 연산에 섞여 KST 새벽에만 게이트가 어긋난다.
    """
    if isinstance(value, datetime):
        raise TypeError(f"{name} 에는 date 가 와야 한다. datetime 을 넘겼다: {value!r}")
    if not isinstance(value, date):
        raise TypeError(f"{name} 에는 date 가 와야 한다: {type(value).__name__}")


# ── 질문 속 나이 (#574) ─────────────────────────────────────────────────────
# "보통 3~4세 어린이들은 …" 처럼 아이 일반을 물을 때, 질문에 적힌 나이를 월령 구간 `[lo, hi)` 로
# 읽는다. 나이 계산은 코드 몫이라(루트 §3) 모델이 나이를 뽑지 않는다. "세 · 살" 은 만 나이로
# 읽는다 — 만 나이 통일 뒤 법적 기준이고, 세 문서의 월령도 만 나이다.
#
# 문서가 다루는 상한(`v1_month_limit`)은 YAML 값이라 이 층에서 모른다. 호출부가 자른다.

_KOREAN_NUMBERS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}
_KOREAN_BUNDLES = {
    "한두": (1, 2),
    "두세": (2, 3),
    "세네": (3, 4),
    "네다섯": (4, 5),
    "대여섯": (5, 6),
}
_DASH = r"\s*[~\-–～]\s*"
# "돌" 은 뒤에 오는 말로 가린다 — 돌봄 · 돌려 · 돌멩이를 나이로 읽지 않는다
_DOL_TAIL = r"(?=\s|$|[?.,!]|아기|쟁이|이|에|때|쯤|전|부터|까지|무렵|즈음|된|지난|지나)"

# 숫자 앞뒤가 소수점이면 나이가 아니다("1.5세"). "세" 는 숫자에 붙여 쓸 때만 나이로 읽는다 —
# "숫자 10 세?" 의 "세" 는 세다(count)의 활용형이다. "살" · "개월" 은 띄어도 나이다
_NUM = r"(?<![\d.])(\d+)(?!\.\d)"
_YEAR_UNIT = r"(?:세|살)"
_AGE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(_NUM + r"(?:\s*개월)?" + _DASH + r"(\d+)\s*개월"), "month_range"),
    (re.compile(_NUM + r"\s*개월"), "month"),
    (
        re.compile(r"(?:만\s*)?" + _NUM + r"(?:\s*살|세)?" + _DASH + r"(\d+)\s*" + _YEAR_UNIT),
        "year_range",
    ),
    (re.compile(r"(?:만\s*)?" + _NUM + r"(?:세|\s*살)"), "year"),
    (re.compile(r"(?<![가-힣])(" + "|".join(_KOREAN_BUNDLES) + r")\s*살"), "bundle"),
    (re.compile(r"(?<![가-힣])(" + "|".join(_KOREAN_NUMBERS) + r")\s*살"), "korean_year"),
    (re.compile(r"(?<![가-힣])(두|세|네|다섯)\s*돌" + _DOL_TAIL), "nth_dol"),
    (re.compile(r"(?<![가-힣])돌" + _DOL_TAIL), "dol"),
)


def _range_of(kind: str, match: re.Match[str]) -> tuple[int, int]:
    if kind == "month_range":
        return int(match[1]), int(match[2]) + 1
    if kind == "month":
        return int(match[1]), int(match[1]) + 1
    if kind == "year_range":
        return int(match[1]) * 12, (int(match[2]) + 1) * 12
    if kind == "year":
        return int(match[1]) * 12, (int(match[1]) + 1) * 12
    if kind == "bundle":
        lo, hi = _KOREAN_BUNDLES[match[1]]
        return lo * 12, (hi + 1) * 12
    if kind == "korean_year":
        years = _KOREAN_NUMBERS[match[1]]
        return years * 12, (years + 1) * 12
    if kind == "nth_dol":
        # 두 돌 = 두 번째 생일 → 만 2세
        years = _KOREAN_NUMBERS[match[1]]
        return years * 12, (years + 1) * 12
    return 12, 24  # 돌 — 첫 생일 무렵, 만 1세


def asked_month_range(text: str) -> tuple[int, int] | None:
    """질문에 적힌 나이를 월령 구간 `[lo, hi)` 로. 나이가 없으면 None.

    - 나이가 둘 이상이면 문장에서 먼저 나온 것. 두 나이를 합쳐 새 구간을 만들지 않는다.
    - 같은 자리에서 시작하면 긴 쪽("3~4세" 가 "3세" 보다 먼저).
    - 거꾸로 쓴 범위("4~3세")는 `lo >= hi` 인 빈 구간 그대로 돌려준다 — 호출부가 자료 없음으로
      닫는다. 고쳐 읽지 않는다.
    """
    found: list[tuple[int, int, tuple[int, int]]] = []
    for pattern, kind in _AGE_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append((match.start(), -(match.end() - match.start()), _range_of(kind, match)))
    if not found:
        return None
    return min(found)[2]


def asked_age_label(lo: int, hi: int) -> str:
    """화면 · 모델 입력에 쓰는 구간 이름 — "만 1세" · "만 3~4세" · "18개월" · "12~18개월"."""
    if hi - lo == 1:
        return f"{lo}개월"
    if lo % 12 == 0 and hi % 12 == 0:
        first, last = lo // 12, hi // 12 - 1
        return f"만 {first}세" if first == last else f"만 {first}~{last}세"
    return f"{lo}~{hi - 1}개월"
