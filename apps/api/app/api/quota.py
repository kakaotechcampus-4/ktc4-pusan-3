"""보호자별 하루 입력 횟수 — 프로세스 메모리.

한 줄 입력 하나가 모델을 최대 9번쯤 부른다(Memory 7 + Supervisor 1~2). MLAPI 크레딧은 월 12만 원이고
넘으면 키가 자동 삭제된다. run 당 상한만으로는 입력을 반복하는 것을 못 막아서 하루 횟수를 센다.
한도는 설정값(INPUT_DAILY_LIMIT)이다 — 근거 없이 정한 테스트용 숫자라 코드 수정 없이 바꾼다.

- 날짜는 호출하는 쪽이 **한국 시간**으로 넘긴다(`today_kst`). UTC 서버에서 date.today() 를 쓰면
  KST 00:00~09:00 에 하루가 어긋난다.
- 🚨 실패한 입력도 센다(돌려주지 않는다). 실패해도 Supervisor 호출 비용은 이미 나갔고, 돌려주면
  이상한 문장을 반복해서 크레딧을 태울 수 있다. 이 한도의 목적은 사용량 과금이 아니라 크레딧 보호다.
- 같은 키 재생은 새 입력이 아니다 — 세는 쪽(접수 창구)이 재생을 먼저 걸러 낸다.

🚨 프로세스 메모리라 서버를 껐다 켜면 0 부터이고, --workers 2 이상이면 프로세스마다 따로다.
   run 채널 · Idempotency 와 같은 전제다 (#134 — api 는 --workers 1).
"""

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

_day: date | None = None
_counts: dict[UUID, int] = {}


def today_kst() -> date:
    return datetime.now(KST).date()


def consume(*, parent_id: UUID, today: date, limit: int) -> bool:
    """한 번을 쓴다. 이미 한도를 채웠으면 쓰지 않고 False.

    날짜가 바뀌면 전날 기록을 통째로 비운다 — 오늘 것만 들고 있어서 메모리가 쌓이지 않는다.
    🚨 확인과 증가 사이에 await 가 없다. 같은 보호자의 요청 둘이 동시에 와도 한도를 넘지 않는다.
    """
    global _day
    if today != _day:
        _counts.clear()
        _day = today
    used = _counts.get(parent_id, 0)
    if used >= limit:
        return False
    _counts[parent_id] = used + 1
    return True


def clear() -> None:
    """전부 비운다. 테스트용."""
    global _day
    _day = None
    _counts.clear()
