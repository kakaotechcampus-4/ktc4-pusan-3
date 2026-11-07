"""화면 가이드를 본 기록 — #696."""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.tour.models import TourSeen
from tests.integration.api.conftest import Bearer, issue_bearer

TOURS = "/api/v1/me/tours"


async def _seen_at(session: AsyncSession, parent_id) -> list:
    rows = await session.scalars(
        select(TourSeen.seen_at)
        .where(TourSeen.parent_id == parent_id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_seeing_again_keeps_the_first_record(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    """? 로 다시 봐도 행은 하나이고 처음 본 시각이 남는다."""
    headers, parent_id = bearer
    assert (await db_client.get(TOURS, headers=headers)).json() == {"seen": []}

    assert (await db_client.put(f"{TOURS}/home-empty", headers=headers)).status_code == 204
    # 테스트 트랜잭션 안에서는 now() 가 그대로라, 처음 본 시각을 지난 값으로 옮겨 두고 본다
    first = datetime(2026, 1, 1, tzinfo=UTC)
    await session.execute(update(TourSeen).values(seen_at=first))
    assert (await db_client.put(f"{TOURS}/home-empty", headers=headers)).status_code == 204
    assert await _seen_at(session, parent_id) == [first]

    assert (await db_client.get(TOURS, headers=headers)).json() == {"seen": ["home-empty"]}


async def test_unknown_tour_is_refused(db_client: AsyncClient, bearer: Bearer):
    headers, _ = bearer
    resp = await db_client.put(f"{TOURS}/not-a-screen", headers=headers)
    assert resp.status_code == 422


async def test_reset_clears_only_my_records(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    other_headers, _ = await issue_bearer(session, token="test-token-other")
    await db_client.put(f"{TOURS}/chat", headers=headers)
    await db_client.put(f"{TOURS}/chat", headers=other_headers)

    assert (await db_client.delete(TOURS, headers=headers)).status_code == 204

    assert (await db_client.get(TOURS, headers=headers)).json() == {"seen": []}
    assert (await db_client.get(TOURS, headers=other_headers)).json() == {"seen": ["chat"]}
