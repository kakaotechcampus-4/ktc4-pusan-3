"""Activity 의 장소 포트(`PlaceSource`)를 적재한 `place` 표로 구현한다 (D9 · #516).

Agent 는 DB 에 직접 닿지 않는다. 포트 모양(`PlaceRow` · `CoarseLocation`)과 반경 · 곳 수 상한은
agents 가 정한 계약이라 여기서 import 한다 — `domains/safety/agent_reader.py` 와 같은 예외 경로다
(apps/api CLAUDE.md "agents 가 정한 저장 계약").

- 🚨 보호자 좌표(`CoarseLocation`)는 이 호출 안에서 거리 계산에만 쓴다. 표 · 로그 · 예외 문구에
  남기지 않는다. DB 오류 글에는 쿼리 인자(좌표)가 실릴 수 있어서, 실패하면 종류 이름만 남기고
  원래 예외를 끊는다(`from None`).
- 반경은 `PLACE_MAX_RADIUS_M` 로 자르고 가까운 순 `PLACE_TOP_K` 곳까지만 돌려준다.
- 놀이터(`PLAYGROUND`)로 찾으면 놀이터가 있는 공원도 온다. 그 행의 종류는 표에 실린 그대로
  (`park`)다.
- 데이터기준일자를 `PlaceRow` 에 싣는다. 모델에는 가지 않는다 — tool 결과는 이름 · 종류뿐이다.
- 🚨 읽기에 실패하면 `UpstreamUnavailable` 이다 — tool 이 UPSTREAM_ERROR 로 바꾸고 모델은 장소가
  필요 없는 활동만 낸다. 빈 목록으로 대신하지 않는다 — 근처에 없는 것과 못 읽은 것은 다르다.
- 호출마다 짧은 세션을 연다. run 세션을 도메인 task 들이 같이 타지 않게 (#257).
- 아직 러너에 붙이지 않았다 — 위치 기능이 열리기 전이다(#451 · #166). `places=None` 그대로다.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.store.ports import (
    PLACE_MAX_RADIUS_M,
    PLACE_TOP_K,
    CoarseLocation,
    PlaceRow,
    UpstreamUnavailable,
)
from app.domains.place import repository

log = logging.getLogger(__name__)

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DbPlaceSource:
    """`PlaceSource`. 장소 행은 근거가 아니다 — 날씨처럼 필터 조건이다 (D9)."""

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def nearby(
        self, *, location: CoarseLocation, category: PlaceCategory | str, radius_m: int
    ) -> list[PlaceRow]:
        # 글자("park")로 와도 받는다 — ToolArgs 가 use_enum_values 라 tool 인자는 글자다(#583 리뷰).
        # 닫힌 값 밖이면 ValueError 다
        category = PlaceCategory(category)
        if radius_m <= 0:
            raise ValueError("반경은 0 보다 커야 한다")
        try:
            async with self._open() as session:
                found = await repository.nearby(
                    session,
                    lat=location.lat,
                    lng=location.lon,
                    category=category.value,
                    radius_m=min(radius_m, PLACE_MAX_RADIUS_M),
                    limit=PLACE_TOP_K,
                )
        except (SQLAlchemyError, OSError) as error:
            log.warning("place 읽기 실패 error=%s", type(error).__name__)
            raise UpstreamUnavailable("장소를 읽지 못했다") from None
        return [
            PlaceRow(
                name=row.name,
                category=PlaceCategory(row.category),
                distance_m=round(row.distance_m),
                source=row.source,  # type: ignore[arg-type] — CHECK 가 값 범위를 막는다
                data_date=row.data_date,
            )
            for row in found
        ]
