"""아이의 월령과 단계를 계산하는 규칙.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

네 도메인 Agent 가 같은 함수를 쓴다. 공통이 정하는 것은 **월령 계산까지**이고,
그 값으로 tool 을 어떻게 가를지는 도메인마다 다르다 (docs/agents/shared/Tool_공통.md §2).

    Food     stage 를 배타적 범주로 — 수유기와 유아식기는 먹을 수 있는 것이 질적으로 다르다
    Growth   tool 별 min_month 눈금 — 열리는 시점만 다르고 배타적인 tool 이 없다
    Health   가르지 않는다 — 검진 차수·접종 시기라는 계산 방식만 바뀐다
    Activity 미정

stage 는 참고값이지 공통 게이팅 축이 아니다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Stage = Literal["infant_milk", "infant_weaning", "toddler", "preschool"]
Band = Literal["infant", "toddler"]

# 단계 경계. 월령 내림차순으로 훑어 처음 걸리는 칸이 그 아이의 stage 다.
# docs/agents/shared/Tool_공통.md §2 의 표와 같은 값이어야 한다.
# TODO: reference/age_gates.yaml이 정본(새로 생성하기)
# 파일이 생기기 전까지 여기가 유일한 자리다 — 코드·프롬프트 어디에도 숫자를 복제하지 않는다.
_STAGE_BOUNDARIES: tuple[tuple[int, Stage], ...] = (
    (36, "preschool"),
    (12, "toddler"),
    (4, "infant_weaning"),
    (0, "infant_milk"),
)

# v1 범위 상한 — 이 월령 미만(만 6세 전)까지가 서비스가 다루는 범위다
# (docs/agents/shared/연령별_Tool_전략.md §2 "72 · v1 범위 상한"). 아이 등록을 막는 값이 아니다 —
# 검진 · 접종 안내가 끝나는 경계이고, 문서 행 월령의 끝이다 (A-6).
V1_MONTH_LIMIT = 72

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


def life_stage(birth_date: date, today: date) -> LifeStage:
    """생년월일과 기준일로 월령·단계를 계산한다.

    today 는 호출하는 쪽이 넘긴다. 이 함수 안에서 date.today() 를 부르지 않는다 —
    UTC 서버에서 부르면 KST 00:00~09:00 사이에 하루가 어긋나 그 시간대에만 게이트가 안 열린다.
    """
    months = months_between(birth_date, today)
    stage = stage_of(months)
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


def stage_of(months: int) -> Stage:
    """월령을 단계로 바꾼다. 경계 월령이 되는 날 다음 단계가 된다."""
    if months < 0:
        raise ValueError(f"월령은 음수일 수 없다: {months}")
    for boundary, stage in _STAGE_BOUNDARIES:
        if months >= boundary:
            return stage
    raise AssertionError("_STAGE_BOUNDARIES 의 마지막 칸이 0 이 아니다")  # pragma: no cover


def first_month_of(stage: Stage) -> int:
    """그 단계가 시작하는 월령. 월령을 모르고 단계만 알 때 가장 어린 쪽으로 볼 때 쓴다."""
    for boundary, candidate in _STAGE_BOUNDARIES:
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
