"""run_hold 읽기 · 쓰기. 세션을 받아 실행만 하고 commit 하지 않는다 — 트랜잭션은 부르는 쪽 몫이다.

`now` 를 안 주면 DB 의 `now()` 로 판정한다. 워커마다 시계가 달라도 판정이 같다. 테스트만 넘긴다.
꺼내지 못하는 이유(없음 · 남의 것 · 다른 아이 · 사용 중 · 만료)는 전부 None 으로 합친다 — 이유를
갈라 알려주면 그 run 이 있는지 없는지가 드러난다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.run_hold.models import HoldKind, RunHold


def _at(now: datetime | None) -> ColumnElement[datetime] | datetime:
    return func.now() if now is None else now


def _open(
    kind: HoldKind, parent_id: uuid.UUID, child_id: uuid.UUID, now: datetime | None
) -> tuple[ColumnElement[bool], ...]:
    """지금 꺼낼 수 있는 행 — 같은 보호자 · 같은 아이 · 사용 중 아님 · 만료 전."""
    return (
        RunHold.kind == kind,
        RunHold.parent_id == parent_id,
        RunHold.child_id == child_id,
        RunHold.in_use.is_(False),
        RunHold.expires_at > _at(now),
    )


async def put_hold(
    session: AsyncSession,
    *,
    kind: HoldKind,
    key: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    payload: dict,
    ttl: timedelta,
    now: datetime | None = None,
) -> None:
    """넣는다. 같은 key 가 있으면 덮고 보관 시간을 새로 센다 — 같은 run 에서 질문이 다시 나온 경우.

    보호자 · 아이가 이미 지워졌으면 FK 오류(IntegrityError)다. 버릴지는 부르는 쪽이 정한다.
    """
    values = {
        "kind": kind,
        "key": key,
        "parent_id": parent_id,
        "child_id": child_id,
        "payload": payload,
        "expires_at": _at(now) + ttl,
        "in_use": False,
    }
    stmt = insert(RunHold).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RunHold.kind, RunHold.key],
        set_={name: stmt.excluded[name] for name in values if name not in ("kind", "key")},
    )
    await session.execute(stmt)


async def get_hold(
    session: AsyncSession,
    *,
    kind: HoldKind,
    key: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    now: datetime | None = None,
) -> dict | None:
    """확인만 한다. "사용 중" 으로 바꾸지 않는다."""
    return await session.scalar(
        select(RunHold.payload).where(RunHold.key == key, *_open(kind, parent_id, child_id, now))
    )


async def claim_hold(
    session: AsyncSession,
    *,
    kind: HoldKind,
    key: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    now: datetime | None = None,
) -> dict | None:
    """꺼내면서 "사용 중" 으로 둔다.

    🚨 확인과 표시가 한 문장이다. 워커 둘에 같은 답이 동시에 와도 행 잠금 뒤 `in_use = false`
       를 다시 보므로 하나만 RETURNING 을 받는다.
    """
    return await session.scalar(
        update(RunHold)
        .where(RunHold.key == key, *_open(kind, parent_id, child_id, now))
        .values(in_use=True)
        .returning(RunHold.payload)
    )


async def release_hold(session: AsyncSession, *, kind: HoldKind, key: str) -> None:
    """표시만 푼다. `expires_at` 은 그대로 — 15분은 처음 넣은 때부터 센다 (#564)."""
    await session.execute(
        update(RunHold).where(RunHold.kind == kind, RunHold.key == key).values(in_use=False)
    )


async def drop_hold(session: AsyncSession, *, kind: HoldKind, key: str) -> None:
    await session.execute(delete(RunHold).where(RunHold.kind == kind, RunHold.key == key))


async def open_hold_keys(
    session: AsyncSession,
    *,
    kind: HoldKind,
    keys: Sequence[str],
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    now: datetime | None = None,
) -> set[str]:
    """그중 지금 꺼낼 수 있는 key. 대화 하루치의 "이어서 답하기" 를 한 번에 본다."""
    if not keys:
        return set()
    rows = await session.scalars(
        select(RunHold.key).where(RunHold.key.in_(keys), *_open(kind, parent_id, child_id, now))
    )
    return set(rows.all())


async def delete_link_holds(
    session: AsyncSession, *, parent_id: uuid.UUID, child_id: uuid.UUID
) -> None:
    """연결 끊기 — 그 보호자의 그 아이 것만, 종류와 상관없이. 보호자 · 아이가 둘 다 남아 CASCADE 가
    걸리지 않는다. 끊기와 같은 트랜잭션에서 부른다."""
    await session.execute(
        delete(RunHold).where(RunHold.parent_id == parent_id, RunHold.child_id == child_id)
    )


async def delete_expired_holds(session: AsyncSession, *, now: datetime | None = None) -> int:
    result = await session.execute(delete(RunHold).where(RunHold.expires_at <= _at(now)))
    return result.rowcount or 0
