"""보호자별 하루 입력 횟수 — 워커 밖 카운터(`rate_event`, #877 3단계).

한 줄 입력 하나가 모델을 최대 9번쯤 부른다(Memory 7 + Supervisor 1~2). MLAPI 크레딧은 월 12만 원이고
넘으면 키가 자동 삭제된다. run 당 상한만으로는 입력을 반복하는 것을 못 막아서 하루 횟수를 센다.
한도는 설정값(INPUT_DAILY_LIMIT)이다 — 근거 없이 정한 테스트용 숫자라 코드 수정 없이 바꾼다.

- 날짜는 호출하는 쪽이 **한국 시간**으로 넘긴다(`today_kst`). UTC 서버에서 date.today() 를 쓰면
  KST 00:00~09:00 에 하루가 어긋난다.
- 🚨 실패한 입력도 센다(돌려주지 않는다). 실패해도 Supervisor 호출 비용은 이미 나갔고, 돌려주면
  이상한 문장을 반복해서 크레딧을 태울 수 있다. 이 한도의 목적은 사용량 과금이 아니라 크레딧 보호다.
- 같은 키 재생은 새 입력이 아니다 — 세는 쪽(접수 창구)이 재생을 먼저 걸러 낸다.
- 🚨 확인과 기록이 한 트랜잭션 · 통마다 잠금이다 — 워커가 여럿이어도 한도를 넘지 않는다
  (`rate_event.repository.take`). 한 번마다 자기 세션으로 바로 commit 한다.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.db import background_session
from app.domains.rate_event.repository import delete_events_with_prefix, take

KST = ZoneInfo("Asia/Seoul")
_PREFIX = "input:"

_session_factory: Callable[[], AsyncSession] = background_session


def today_kst() -> date:
    return datetime.now(KST).date()


async def consume(*, parent_id: UUID, today: date, limit: int, now: datetime | None = None) -> bool:
    """한 번을 쓴다. 이미 한도를 채웠으면 쓰지 않고 False. 날짜가 통을 가른다 — 다음 날은 0부터."""
    async with _session_factory() as session:
        taken = await take(
            session,
            limits=[(f"{_PREFIX}{parent_id}:{today.isoformat()}", limit)],
            since=None,
            at=now or datetime.now(UTC),
        )
        await session.commit()
    return taken


async def reset() -> None:
    """하루 입력 한도를 모두 비운다. eval 이 한도를 끄고 돌 때 쓴다 — 서버 코드는 부르지 않는다."""
    async with _session_factory() as session:
        await delete_events_with_prefix(session, prefix=_PREFIX)
        await session.commit()
