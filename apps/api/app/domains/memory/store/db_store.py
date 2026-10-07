"""MemoryStore Protocol 의 SQLAlchemy 구현.

세션은 밖에서 받는다 (DbCuratorStore 패턴과 동일).
트랜잭션 경계는 호출하는 쪽이 정한다.

InMemoryStore 와 동일한 fields 매핑을 유지한다 -- Memory Agent 도구가
row.fields["subject"], row.fields["status"] 등으로 읽기 때문이다.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.datetime_rules import DateRange
from app.agents.memory.store.ports import (
    EventItemRow,
    EventRow,
    ObservationDomain,
    ObservationRow,
)
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
    ObservationStatus,
    Promotable,
)
from app.domains.schedule.models import Event, EventItem

_KST = timezone(timedelta(hours=9))


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


_MODEL_BY_DOMAIN: dict[str, type] = {
    "food": ObservationFood,
    "health": ObservationHealth,
    "education": ObservationEducation,
    "activity": ObservationActivity,
    "routine": ObservationRoutine,
}

# 관찰 모델에서 fields dict 를 만들 때 제외하는 키.
# ObservationRow 의 최상위 속성(id, domain, raw_text, created_at, observed_on)에
# 대응하는 ORM 컬럼이다.
_ROW_TOP_KEYS = frozenset({"id", "raw_text", "created_at"})


def _daterange_to_psycopg(dr: DateRange) -> Range[date]:
    return Range(dr.start, dr.end)


def _psycopg_to_daterange(r: Range[date]) -> DateRange:
    return DateRange(start=r.lower, end=r.upper)


def _observed_on_from_range(r: Range[date]) -> date:
    return r.lower


def _row_from_orm(domain: str, orm: Any) -> ObservationRow:
    """ORM 인스턴스 -> ObservationRow. InMemoryStore 와 같은 fields 키를 만든다."""
    fields: dict[str, Any] = {}
    for col in orm.__table__.columns:
        key = col.key
        if key in _ROW_TOP_KEYS:
            continue
        val = getattr(orm, key)
        if key == "child_id":
            fields[key] = str(val)
        elif key == "source_writer":
            fields[key] = str(val) if val is not None else None
        elif key == "observed_range":
            fields[key] = _psycopg_to_daterange(val)
        elif key == "status":
            fields[key] = val.value if hasattr(val, "value") else str(val)
        else:
            # enum 값은 .value 로 풀어야 tool 이 문자열로 비교할 수 있다
            fields[key] = val.value if hasattr(val, "value") else val
    return ObservationRow(
        id=str(orm.id),
        domain=domain,
        raw_text=orm.raw_text,
        created_at=orm.created_at,
        observed_on=_observed_on_from_range(orm.observed_range),
        fields=fields,
    )


def _enum_val(v: Any) -> Any:
    return v.value if hasattr(v, "value") else str(v)


def _event_row(orm: Event) -> EventRow:
    return EventRow(
        id=str(orm.id),
        title=orm.title,
        starts_at=orm.starts_at.astimezone(_KST) if orm.starts_at else orm.starts_at,
        ends_at=orm.ends_at.astimezone(_KST) if orm.ends_at else orm.ends_at,
        all_day=orm.all_day,
        fields={
            "child_id": str(orm.child_id),
            "event_type": _enum_val(orm.event_type),
            "category": _enum_val(orm.category),
            "created_by": _enum_val(orm.created_by),
        },
    )


def _event_item_row(orm: EventItem) -> EventItemRow:
    return EventItemRow(
        item_id=str(orm.item_id),
        event_id=str(orm.event_id),
        item_name=orm.item_name,
        is_prepared=orm.is_prepared,
        prepared_at=orm.prepared_at,
    )


class DbMemoryStore:
    """MemoryStore Protocol 구현. 세션 하나가 run 하나를 의미한다."""

    def __init__(self, session: AsyncSession, *, child_id: UUID) -> None:
        self._session = session
        self._child_id = child_id

    # ── observation ─────────────────────────────────────────────

    async def create_observation(
        self,
        *,
        domain: ObservationDomain,
        child_id: UUID,
        source_writer: UUID,
        raw_text: str,
        observed_on: date,
        observed_range: DateRange,
        fields: dict[str, Any],
    ) -> ObservationRow:
        assert child_id == self._child_id, "child_id mismatch"
        model = _MODEL_BY_DOMAIN[domain]
        # fields 에서 상위 파라미터로 이미 받는 키를 빼고 도메인 컬럼만 남긴다
        col_fields = {
            k: v
            for k, v in fields.items()
            if k not in {"child_id", "source_writer", "observed_range", "status"}
        }
        orm = model(
            child_id=child_id,
            source_writer=source_writer,
            raw_text=raw_text,
            observed_range=_daterange_to_psycopg(observed_range),
            **col_fields,
        )
        self._session.add(orm)
        await self._session.flush()
        return _row_from_orm(domain, orm)

    async def query_observations(
        self,
        *,
        domain: ObservationDomain,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        raw_text_query: str | None = None,
    ) -> list[ObservationRow]:
        assert child_id == self._child_id, "child_id mismatch"
        model = _MODEL_BY_DOMAIN[domain]
        stmt = select(model).where(
            model.child_id == child_id,
            model.status != ObservationStatus.DELETED,
        )
        if date_from is not None:
            stmt = stmt.where(
                model.observed_range.op("&&")(func.daterange(date_from, None, "[)"))
            )
        if date_to is not None:
            dto_exclusive = date_to + timedelta(days=1)
            stmt = stmt.where(
                model.observed_range.op("&&")(func.daterange(None, dto_exclusive, "[)"))
            )
        if raw_text_query:
            stmt = stmt.where(model.raw_text.contains(raw_text_query, autoescape=True))
        stmt = stmt.order_by(func.lower(model.observed_range), model.id)
        rows = (await self._session.scalars(stmt)).all()
        return [_row_from_orm(domain, r) for r in rows]

    async def get_observation(
        self, *, domain: ObservationDomain, observation_id: str
    ) -> ObservationRow | None:
        uid = _parse_uuid(observation_id)
        if uid is None:
            return None
        model = _MODEL_BY_DOMAIN[domain]
        orm = await self._session.scalar(
            select(model).where(
                model.id == uid,
                model.child_id == self._child_id,
                model.status != ObservationStatus.DELETED,
            )
        )
        if orm is None:
            return None
        return _row_from_orm(domain, orm)

    async def update_observation(
        self,
        *,
        domain: ObservationDomain,
        observation_id: str,
        fields: dict[str, Any],
        observed_on: date | None = None,
        clear: frozenset[str] = frozenset(),
    ) -> ObservationRow | None:
        uid = _parse_uuid(observation_id)
        if uid is None:
            return None
        model = _MODEL_BY_DOMAIN[domain]
        values: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "observed_range" and isinstance(v, DateRange):
                values["observed_range"] = _daterange_to_psycopg(v)
            else:
                values[k] = v
        for k in clear:
            values[k] = None
        if observed_on is not None and "observed_range" not in values:
            values["observed_range"] = Range(observed_on, observed_on + timedelta(days=1))
        if not values:
            return await self.get_observation(domain=domain, observation_id=observation_id)
        orm = await self._session.scalar(
            update(model)
            .where(
                model.id == uid,
                model.child_id == self._child_id,
                model.status != ObservationStatus.DELETED,
            )
            .values(**values)
            .returning(model)
        )
        return _row_from_orm(domain, orm) if orm is not None else None

    async def delete_observation(
        self, *, domain: ObservationDomain, observation_id: str
    ) -> bool:
        uid = _parse_uuid(observation_id)
        if uid is None:
            return False
        model = _MODEL_BY_DOMAIN[domain]
        values: dict[str, Any] = {"status": ObservationStatus.DELETED}
        if issubclass(model, Promotable):
            values["embedding"] = None
        result = await self._session.scalar(
            update(model)
            .where(
                model.id == uid,
                model.child_id == self._child_id,
                model.status != ObservationStatus.DELETED,
            )
            .values(**values)
            .returning(model.id)
        )
        if result is not None:
            from app.domains.memory.observation.models import ObservationLinkHold

            await self._session.execute(
                delete(ObservationLinkHold).where(
                    ObservationLinkHold.domain == domain,
                    ObservationLinkHold.observation_id == uid,
                )
            )
        return result is not None

    # ── event ───────────────────────────────────────────────────

    async def query_events(
        self,
        *,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        title_query: str | None = None,
    ) -> list[EventRow]:
        assert child_id == self._child_id, "child_id mismatch"
        stmt = select(Event).where(Event.child_id == child_id)
        if date_from is not None:
            kst_start = datetime.combine(date_from, time.min, tzinfo=_KST)
            stmt = stmt.where(Event.starts_at >= kst_start)
        if date_to is not None:
            kst_end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=_KST)
            stmt = stmt.where(Event.starts_at < kst_end)
        if title_query:
            stmt = stmt.where(Event.title.contains(title_query, autoescape=True))
        stmt = stmt.order_by(Event.starts_at, Event.id)
        rows = (await self._session.scalars(stmt)).all()
        return [_event_row(r) for r in rows]

    async def get_event(self, *, event_id: str) -> EventRow | None:
        uid = _parse_uuid(event_id)
        if uid is None:
            return None
        orm = await self._session.scalar(
            select(Event).where(Event.id == uid, Event.child_id == self._child_id)
        )
        return _event_row(orm) if orm is not None else None

    async def delete_event(self, *, event_id: str) -> bool:
        uid = _parse_uuid(event_id)
        if uid is None:
            return False
        orm = await self._session.scalar(
            select(Event).where(Event.id == uid, Event.child_id == self._child_id)
        )
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    # ── event_item ──────────────────────────────────────────────

    async def get_event_item(self, *, item_id: str) -> EventItemRow | None:
        uid = _parse_uuid(item_id)
        if uid is None:
            return None
        # EventItem has no child_id; join through Event to enforce ownership.
        orm = await self._session.scalar(
            select(EventItem)
            .join(Event, EventItem.event_id == Event.id)
            .where(EventItem.item_id == uid, Event.child_id == self._child_id)
        )
        return _event_item_row(orm) if orm is not None else None

    async def list_event_items(self, *, event_id: str) -> list[EventItemRow]:
        uid = _parse_uuid(event_id)
        if uid is None:
            return []
        rows = (
            await self._session.scalars(
                select(EventItem)
                .join(Event, EventItem.event_id == Event.id)
                .where(EventItem.event_id == uid, Event.child_id == self._child_id)
                .order_by(EventItem.item_id)
            )
        ).all()
        return [_event_item_row(r) for r in rows]

    async def update_event_item(
        self, *, item_id: str, fields: dict[str, Any]
    ) -> EventItemRow | None:
        uid = _parse_uuid(item_id)
        if uid is None:
            return None
        # EventItem has no child_id; join through Event to enforce ownership.
        orm = await self._session.scalar(
            select(EventItem)
            .join(Event, EventItem.event_id == Event.id)
            .where(EventItem.item_id == uid, Event.child_id == self._child_id)
        )
        if orm is None:
            return None
        for k, v in fields.items():
            setattr(orm, k, v)
        await self._session.flush()
        return _event_item_row(orm)

    async def delete_event_item(self, *, item_id: str) -> bool:
        uid = _parse_uuid(item_id)
        if uid is None:
            return False
        # EventItem has no child_id; join through Event to enforce ownership.
        orm = await self._session.scalar(
            select(EventItem)
            .join(Event, EventItem.event_id == Event.id)
            .where(EventItem.item_id == uid, Event.child_id == self._child_id)
        )
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True
