"""run_hold 읽기 · 쓰기. 세션을 받아 실행만 하고 commit 하지 않는다 — 트랜잭션은 부르는 쪽 몫이다.

`now` 를 안 주면 DB 의 `now()` 로 판정한다. 워커마다 시계가 달라도 판정이 같다. 테스트만 넘긴다.
꺼내지 못하는 이유(없음 · 남의 것 · 다른 아이 · 사용 중 · 만료)는 전부 None 으로 합친다 — 이유를
갈라 알려주면 그 run 이 있는지 없는지가 드러난다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.run_hold.models import HoldKind, RunHold


def _at(now: datetime | None) -> ColumnElement[datetime] | datetime:
    return func.now() if now is None else now


def _owned(
    kind: HoldKind, parent_id: uuid.UUID, child_id: uuid.UUID | None, now: datetime | None
) -> tuple[ColumnElement[bool], ...]:
    """같은 보호자 · (주면) 같은 아이 · 만료 전. 사진 run 은 아이를 모르는 주소라 보호자만 본다."""
    where = [RunHold.kind == kind, RunHold.parent_id == parent_id, RunHold.expires_at > _at(now)]
    if child_id is not None:
        where.append(RunHold.child_id == child_id)
    return tuple(where)


def _open(
    kind: HoldKind, parent_id: uuid.UUID, child_id: uuid.UUID | None, now: datetime | None
) -> tuple[ColumnElement[bool], ...]:
    """지금 꺼낼 수 있는 행 — 주인 확인 + 사용 중 아님."""
    return (*_owned(kind, parent_id, child_id, now), RunHold.in_use.is_(False))


def _typed(payload_type: str | None) -> tuple[ColumnElement[bool], ...]:
    """payload 의 `type` 칸으로 한 칸 안의 종류를 가른다 (05 의 실패 맥락 · 0개 · 닫힘 기록)."""
    return () if payload_type is None else (RunHold.payload["type"].astext == payload_type,)


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
    keep_expiry_of_type: str | None = None,
) -> None:
    """넣는다. 같은 key 가 있으면 덮고 보관 시간을 새로 센다 — 같은 run 에서 질문이 다시 나온 경우.

    `keep_expiry_of_type` 을 주면, 덮이는 행이 같은 주인의 만료 전 그 type 일 때 만료 시각을
    이어받는다 — 05 실패 맥락은 다시 넣어도 15분을 처음 넣은 때부터 센다.
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
    updates = {name: stmt.excluded[name] for name in values if name not in ("kind", "key")}
    if keep_expiry_of_type is not None:
        kept = and_(
            RunHold.payload["type"].astext == keep_expiry_of_type,
            RunHold.parent_id == stmt.excluded.parent_id,
            RunHold.child_id == stmt.excluded.child_id,
            RunHold.expires_at > _at(now),
        )
        updates["expires_at"] = case((kept, RunHold.expires_at), else_=stmt.excluded.expires_at)
    await session.execute(
        stmt.on_conflict_do_update(index_elements=[RunHold.kind, RunHold.key], set_=updates)
    )


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
    payload_type: str | None = None,
) -> dict | None:
    """꺼내면서 "사용 중" 으로 둔다.

    🚨 확인과 표시가 한 문장이다. 워커 둘에 같은 답이 동시에 와도 행 잠금 뒤 `in_use = false`
       를 다시 보므로 하나만 RETURNING 을 받는다.
    """
    return await session.scalar(
        update(RunHold)
        .where(RunHold.key == key, *_open(kind, parent_id, child_id, now), *_typed(payload_type))
        .values(in_use=True)
        .returning(RunHold.payload)
    )


async def hold_in_use(
    session: AsyncSession,
    *,
    kind: HoldKind,
    key: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    now: datetime | None = None,
    payload_type: str | None = None,
) -> bool:
    """주인의 만료 전 행이 "사용 중" 인가. claim 이 None 일 때 없음과 사용 중을 가르는 데 쓴다."""
    found = await session.scalar(
        select(RunHold.key).where(
            RunHold.key == key,
            *_owned(kind, parent_id, child_id, now),
            *_typed(payload_type),
            RunHold.in_use.is_(True),
        )
    )
    return found is not None


async def share_holder_child(
    session: AsyncSession,
    *,
    kind: HoldKind,
    key: str,
    parent_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """그 행의 아이 행을 `FOR KEY SHARE` 로 먼저 잡는다. 아이가 없으면(지워졌다) False.

    🚨 `lock_hold` 보다 먼저 부른다. 아이 삭제는 아이 행 → (CASCADE) 이 행 순서로 잠그므로, 이 행을
       먼저 잡고 나중에 아이 행을 잡으면(공지 · 급식을 넣는 FK 검사) 겹칠 때 교착이다.
       같은 순서(아이 → 이 행)로 잡으면 한쪽이 기다릴 뿐이다.
    """
    child_id = await session.scalar(
        select(RunHold.child_id).where(RunHold.key == key, *_owned(kind, parent_id, None, now))
    )
    if child_id is None:
        return False
    held = await session.scalar(
        text("SELECT id FROM child WHERE id = :id FOR KEY SHARE"), {"id": child_id}
    )
    return held is not None


async def lock_hold(
    session: AsyncSession,
    *,
    kind: HoldKind,
    key: str,
    parent_id: uuid.UUID,
    now: datetime | None = None,
) -> RunHold | None:
    """주인의 만료 전 행을 `FOR UPDATE` 로 잡는다 — 부르는 트랜잭션이 끝날 때까지 같은 행을 잡으려는
    다른 요청은 기다린다. 사진 저장처럼 "확인 → 쓰기 → 결과 남기기" 를 한 번만 하게 할 때 쓴다."""
    return await session.scalar(
        select(RunHold)
        .where(RunHold.key == key, *_owned(kind, parent_id, None, now))
        .with_for_update()
    )


async def set_payload(session: AsyncSession, *, kind: HoldKind, key: str, payload: dict) -> None:
    """내용만 바꾼다. 만료 시각 · 사용 중 표시는 그대로."""
    await session.execute(
        update(RunHold).where(RunHold.kind == kind, RunHold.key == key).values(payload=payload)
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
