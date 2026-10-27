"""Agent 가 읽는 추천 이력 포트 둘을 `suggestion` 표로 구현한다.

- Food 의 `SuggestionHistoryReader`(`DbSuggestionHistory`) — 같은 메뉴를 며칠 안에 다시 내지
  않으려고 읽는다. 아래 Food 설명.
- Growth 의 `IssuedBookReader`(`DbIssuedBooks`) — "다른 책도" 에 같은 책이 다시 나오지 않게 만료 전
  Growth 추천의 `items` 를 읽는다.

Food 는 같은 메뉴를 며칠 안에 다시 내지 않으려고 읽는다(`meal_pool.RECENT_DAYS`). `suggestion` 에는
`menu_key` 칸이 없고 Food 는 `content` 에 메뉴 이름(`display_name`)을 그대로 쓴다 — 그래서 읽을 때
`normalize_menu_key` 로 바꾼다. 레시피 행의 `menu_key` 도 같은 함수로 만든 값이라 서로 만난다.

- draft · rejected · expired 는 센다 — 거절한 메뉴를 바로 다시 내면 보호자에게는 같은 추천의
  반복이다. approved 는 세지 않는다 — 고른 메뉴는 다시 나와도 된다(#442).
- `since` 는 Food 의 오늘(KST)에서 센 날짜다. 그 날짜의 KST 자정부터 센다 — `created_at` 은 UTC 로
  저장되므로 날짜만 잘라 비교하면 KST 새벽이 하루 밀린다.
- 읽기 실패는 그대로 올린다(빈 이력이면 같은 메뉴가 다시 나간다 — 안전 문제는 아니지만 실패가
  가려진다). 호출마다 짧은 세션이다(#257).

이 파일은 agents 를 import 하지 않는다 — 돌려주는 것이 문자열 집합이라서다.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.suggestion.models import Suggestion, SuggestionAgent, SuggestionStatus
from app.rules.menu_key import normalize_menu_key

KST = ZoneInfo("Asia/Seoul")

SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DbSuggestionHistory:
    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def recent_menu_keys(self, *, child_id: UUID, since: date) -> frozenset[str]:
        start = datetime.combine(since, time.min, tzinfo=KST)
        stmt = select(Suggestion.content).where(
            Suggestion.child_id == child_id,
            Suggestion.agent == SuggestionAgent.FOOD,
            Suggestion.status != SuggestionStatus.APPROVED,
            Suggestion.created_at >= start,
        )
        async with self._open() as session:
            contents = (await session.scalars(stmt)).all()
        return frozenset(key for content in contents if (key := normalize_menu_key(content)))


class DbIssuedBooks:
    """Growth 의 이미 낸 책 포트를 `suggestion` 표로.

    만료 전(`expires_at > now`) Growth 추천의 `items` 를 상태와 상관없이 모은다 — 승인 · 거절한 책도
    다시 나오지 않게. 도서 추천의 `items` 는 "제목(저자)" 하나다. 교육 준비물도 같은 칸이라 섞여
    오지만 "제목(저자)" 와 겹칠 일이 드물다. draft 만 만료를 거르는 목록 함수를 쓰지 않는다 — 승인한
    책을 놓친다. 읽기 실패는 그대로 올린다 — 빈 집합이면 같은 책이 다시 나간다. 호출마다 짧은
    세션이다(#257).
    """

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def labels(self, *, child_id: UUID, now: datetime) -> frozenset[str]:
        stmt = select(Suggestion.items).where(
            Suggestion.child_id == child_id,
            Suggestion.agent == SuggestionAgent.GROWTH,
            Suggestion.expires_at > now,
        )
        async with self._open() as session:
            rows = (await session.scalars(stmt)).all()
        return frozenset(item for items in rows for item in items)
