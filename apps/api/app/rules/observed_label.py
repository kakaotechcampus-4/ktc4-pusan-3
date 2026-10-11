"""관찰 날짜를 화면 문구("오늘" · "어제" · "3일 전" · "2주 전")로 바꾸는 규칙.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

화면은 날짜를 계산하지 않고 이 문구를 그대로 그린다 (apps/web `lib/format.ts`). 기억 문장의
"마지막은 어제예요" 도 같은 함수를 쓴다 — 두 화면이 같은 날을 다르게 부르지 않게 한다.

`today` 는 호출하는 쪽이 **한국 시간**으로 넘긴다. UTC 서버의 date.today() 는 KST 00:00~09:00 에
하루가 어긋난다.
"""

from datetime import date

# 이 일수부터 주 단위로 부른다. 목 서버(apps/web `mocks/fixtures.ts` observedLabel)와 같은 값이다.
_DAYS_BEFORE_WEEKS = 7


def observed_label(day: date, *, today: date) -> str:
    """`day` 가 `today` 로부터 얼마 전인지. 미래 날짜는 "오늘" 로 부른다.

    관찰 날짜가 오늘보다 뒤인 일은 정상 경로에 없지만(관찰은 이미 일어난 일이다), 서버와 기기
    시계가 어긋난 경계에서 "-1일 전" 을 그리지 않게 막는다.
    """
    days = (today - day).days
    if days <= 0:
        return "오늘"
    if days == 1:
        return "어제"
    if days < _DAYS_BEFORE_WEEKS:
        return f"{days}일 전"
    return f"{days // _DAYS_BEFORE_WEEKS}주 전"
