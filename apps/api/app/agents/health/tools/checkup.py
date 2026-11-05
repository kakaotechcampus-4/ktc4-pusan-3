"""검진 차수 · 권장 접종 계산 — `compute_checkup_schedule` · `compute_vaccine_schedule`.

생일과 오늘만으로 정한다. 모델도 포트도 부르지 않는 순수 함수이고, **결과를 저장하지 않는다** —
차수를 저장하면 생일을 고칠 때 어긋난다. 기준표는 `reference.py` 가 읽는 yaml 이 정본이다.

- 월령은 민법 달력 계산(`months_between`)이다. `(today - birth).days // 30` 은 쓰지 않는다.
  1차(생후 14~35일)만 일 수다 — 태어난 날이 0일이다.
- 기준일 `today` 는 호출하는 쪽이 아이 타임존의 오늘로 넘긴다. UTC 날짜로 세면 KST 자정 직후
  생일 전환이 하루 밀린다.
- 기간 끝은 "last 개월이 끝나는 날" 이다 — `date_at_month(birth, last + 1)` 의 전날. 이 해석은
  `reference/checkup_nhis.yaml` 머리 주석에 이유가 있다.
- 접종의 "이 시기" 는 지금 월령이 표의 `[first_month, last_month]` 안인 칸이다. 맞았는지는 모른다 —
  기록 연동이 없어서 "놓쳤다" 같은 판정을 만들 근거가 없다.
- "다음 접종 시기" 는 지금 월령 뒤에 처음 열리는 칸이다. 같은 월령에 열리는 칸은 한 번에 묶는다.
  표 끝(만 12세) 뒤에는 없다.
- 72개월부터는 검진이 대상 종료다. 차수를 내지 않는다. 접종은 계속된다 — 표가 만 12세(155개월)
  까지라 만 4~6세 칸(~83개월)과 만 11~12세 칸이 이어서 열린다.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.agents.health.reference import (
    CheckupRound,
    CheckupTable,
    VaccineDose,
    VaccineTable,
    checkup_table,
    vaccine_table,
)
from app.rules.age import date_at_month, months_between

AGED_OUT_MONTH = 72


@dataclass(frozen=True)
class RoundWindow:
    round: CheckupRound
    starts_on: date
    ends_on: date  # 포함


@dataclass(frozen=True)
class NextVaccines:
    starts_on: date  # 그 칸이 열리는 날 (그 월령의 생일)
    doses: tuple[VaccineDose, ...]  # 같은 월령에 같이 열리는 접종


@dataclass(frozen=True)
class CheckupStatus:
    current: RoundWindow | None  # 일반검진 지금 차수
    oral: RoundWindow | None  # 같은 날 구강검진 기간이면
    next: RoundWindow | None  # 다음 일반검진 차수
    aged_out: bool  # 72개월 이상


def _window(row: CheckupRound, birth_date: date) -> RoundWindow:
    if row.start == "day":
        return RoundWindow(
            row,
            starts_on=birth_date + timedelta(days=row.first),
            ends_on=birth_date + timedelta(days=row.last),
        )
    return RoundWindow(
        row,
        starts_on=date_at_month(birth_date, row.first),
        ends_on=date_at_month(birth_date, row.last + 1) - timedelta(days=1),
    )


def compute_checkup_schedule(
    birth_date: date, today: date, table: CheckupTable | None = None
) -> CheckupStatus:
    """지금 · 다음 검진 차수. `today` 는 아이 타임존의 오늘이다."""
    if months_between(birth_date, today) >= AGED_OUT_MONTH:
        return CheckupStatus(current=None, oral=None, next=None, aged_out=True)

    rounds = (table or checkup_table()).rounds
    general = [_window(row, birth_date) for row in rounds if row.kind == "general"]
    oral = [_window(row, birth_date) for row in rounds if row.kind == "oral"]
    return CheckupStatus(
        current=next((w for w in general if w.starts_on <= today <= w.ends_on), None),
        oral=next((w for w in oral if w.starts_on <= today <= w.ends_on), None),
        next=next((w for w in general if w.starts_on > today), None),
        aged_out=False,
    )


def compute_vaccine_schedule(
    birth_date: date, today: date, table: VaccineTable | None = None
) -> tuple[VaccineDose, ...]:
    """지금 월령이 권장 시기 안인 접종. 표 끝(만 12세) 뒤에는 ()."""
    months = months_between(birth_date, today)
    return tuple(
        dose
        for dose in (table or vaccine_table()).doses
        if dose.first_month <= months <= dose.last_month
    )


def compute_next_vaccines(
    birth_date: date, today: date, table: VaccineTable | None = None
) -> NextVaccines | None:
    """지금 월령 뒤에 처음 열리는 접종 칸과 그 시작일. 더 열릴 칸이 없으면 None."""
    months = months_between(birth_date, today)
    later = [dose for dose in (table or vaccine_table()).doses if dose.first_month > months]
    if not later:
        return None
    first = min(dose.first_month for dose in later)
    return NextVaccines(
        starts_on=date_at_month(birth_date, first),
        doses=tuple(dose for dose in later if dose.first_month == first),
    )
