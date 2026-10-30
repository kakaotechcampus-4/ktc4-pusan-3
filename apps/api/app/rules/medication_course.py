"""복약 코스의 기간 규칙. 표준 라이브러리만 쓴다 — 날짜 계산은 모델에게 묻지 않는다."""

from collections.abc import Iterable
from datetime import date, timedelta

# 코스는 처방 1건에 가깝다. 올리는 것은 쉽고 줄이는 것은 저장된 데이터와 부딪힌다
MAX_SPAN_DAYS = 90


# 길이는 글자 수다. title 은 일정 제출, note 는 일기 본문과 같고 나머지는 선례 없는 제안값이다
TITLE_MAX = 200
DOSAGE_MAX = 200
INDICATION_MAX = 500
NOTE_MAX = 1000
DOSES_MAX = 24  # 하루 시각 패턴 개수


def course_field_errors(
    *,
    title: str,
    dosage: str | None,
    indication_text: str | None,
    note: str | None,
    starts_on: date,
    ends_on: date,
    dose_count: int,
) -> list[str]:
    """잘못된 칸의 이름 목록(없으면 빈 목록). 이름은 400 `detail.fields` 에 그대로 실린다.

    순서는 고정이고 같은 칸은 한 번만 나온다. 빈 문자열인 선택 칸은 null 로 저장하니 오류가 아니다.
    """
    wrong: list[str] = []
    if not title.strip() or len(title) > TITLE_MAX:
        wrong.append("schedule.title")
    for name, value, limit in (
        ("dosage", dosage, DOSAGE_MAX),
        ("indication_text", indication_text, INDICATION_MAX),
        ("note", note, NOTE_MAX),
    ):
        if value is not None and len(value) > limit:
            wrong.append(f"schedule.{name}")
    if ends_on < starts_on or span_days(starts_on, ends_on) > MAX_SPAN_DAYS:
        wrong += ["schedule.starts_on", "schedule.ends_on"]
    if not 1 <= dose_count <= DOSES_MAX:
        wrong.append("doses")
    return wrong


def span_days(starts_on: date, ends_on: date) -> int:
    """기간의 날수. 시작일과 끝일을 둘 다 센다."""
    if ends_on < starts_on:
        raise ValueError("ends_on 은 starts_on 이상이어야 한다")
    return (ends_on - starts_on).days + 1


def dose_dates(starts_on: date, ends_on: date, *, today: date) -> list[date]:
    """회차를 만들 날짜. 지난 날짜는 만들지 않고 오늘은 포함한다 (`today` 는 KST 오늘)."""
    span_days(starts_on, ends_on)  # 뒤바뀐 기간은 여기서 막힌다
    first = max(starts_on, today)
    return [first + timedelta(days=n) for n in range((ends_on - first).days + 1)]


def pattern_day(days: Iterable[date], *, today: date) -> date | None:
    """하루 시각 패턴(`notice_times` · Agent 의 `pattern`)을 읽을 날. 회차가 없으면 None.

    오늘 **다음 날부터** 첫 날이다. 수정은 앞으로의 날을 새 패턴으로 다시 만들지만 지난 날과 오늘의
    이미 지난 회차는 옛 시각으로 남아서, 거기서 읽으면 바뀐 시각이 아닌 옛 시각이 나온다. 다음 날이
    없으면(오늘 끝나거나 이미 끝났다) 마지막 날이다 — 마지막 날이 오늘이고 일부 회차가 이미 지났으면
    옛 시각이 섞일 수 있다. 화면과 Agent 가 같은 날을 읽도록 둘이 이 함수를 쓴다.
    """
    unique = set(days)
    if not unique:
        return None
    later = [day for day in unique if day > today]
    return min(later) if later else max(unique)
