"""캘린더 · 일기가 받는 날짜 범위 — #318.

정책이 아니라 계산 한계다. 끝 연도(1 · 9999년)에서는 다음 날 · KST 자정 계산이 넘쳐 500 이 되므로
DB 조회 전에 막는다. Agent 날짜 규칙(agents/common/datetime_rules.py 의 _MIN_YEAR · _MAX_YEAR)과
같은 범위라, Agent 가 저장할 수 있는 일정은 모두 캘린더에서 열린다. agents 내부는 가져올 수 없어
값을 따로 둔다.

🚨 캘린더와 일기가 같은 범위를 쓴다 — 어긋나면 써 둔 일기를 하루 보기에서 못 연다.
   상태 코드는 같은 자리의 형식 오류와 같다 — 쿼리 값은 400, 경로 값은 422 (errors.py · 명세 §8-1).
"""

import datetime as dt

from app.api.errors import ApiError

FIRST_DAY = dt.date(dt.MINYEAR + 1, 1, 1)
LAST_DAY = dt.date(dt.MAXYEAR - 1, 12, 31)


def check_day(day: dt.date, *, status: int) -> None:
    if not FIRST_DAY <= day <= LAST_DAY:
        raise out_of_range(status)


def out_of_range(status: int) -> ApiError:
    return ApiError(status, "validation_failed", "날짜가 올바르지 않아요")
