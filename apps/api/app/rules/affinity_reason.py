"""기억(profile_affinity) 카드의 문구와 낡음 판정.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

🚨 문구에는 **볼 때도 맞는 사실만** 쓴다 — 묶인 기록 수와 마지막 기록 날짜. 승격 조건
   ("최근 2주 안에 3일")은 쓰지 않는다. 상태는 재계산 때마다 최근 14일로 새로 정해지고
   (`rules/profile.py`), 시간 경과 재계산 배치가 아직 없어 DB 의 state 가 오래 남는다.
   화면을 여는 시점에 창을 다시 세면 state 와 숫자가 어긋난다.
🚨 한 번의 관찰을 성향으로 말하지 않는다 (루트 CLAUDE.md §2) — 이 문구는 사실만 말하고
   "좋아해요" 같은 판정은 화면의 상태 라벨이 진다.
"""

from datetime import date

from app.rules import staleness
from app.rules.age import months_between
from app.rules.observed_label import observed_label


def state_reason(*, observation_count: int, last_observed_on: date, today: date) -> str:
    """기억 카드의 한 줄. 예) "지금까지 4번 기록됐고, 마지막은 어제예요".

    묶인 기록이 0개인 기억은 부르지 않는다 — 근거 없는 기억은 목록에서 빠진다
    (routers/affinities.py).
    """
    if observation_count < 1:
        raise ValueError("묶인 기록이 없는 기억에는 문구를 만들지 않는다")
    # 6개월이 지난 기억은 "31주 전" 보다 개월로 말해야 읽힌다. 화면은 이 문구로 낡음을 알린다.
    when = (
        f"{months_between(last_observed_on, today)}개월 전"
        if is_stale(last_observed_on, today=today)
        else observed_label(last_observed_on, today=today)
    )
    last = f"마지막은 {when}{_copula(when)}"
    if observation_count == 1:
        return f"아직 한 번 기록됐어요. {last}"
    return f"지금까지 {observation_count}번 기록됐고, {last}"


def is_stale(last_observed_on: date, *, today: date) -> bool:
    """마지막 기록이 달력 기준 6개월 이상 전이면 True — `rules/staleness.is_stale` 과 같은 판정.

    기억 카드 · 추천 근거 · Agent 가 한 함수를 쓴다. 기억 카드는 키워드 인자로 부른다.
    """
    return staleness.is_stale(last_observed_on, today)


def _copula(word: str) -> str:
    """받침이 있으면 "이에요", 없으면 "예요". 한글이 아닌 글자로 끝나면 "이에요"."""
    last = word[-1]
    if not "가" <= last <= "힣":
        return "이에요"
    return "이에요" if (ord(last) - ord("가")) % 28 else "예요"
