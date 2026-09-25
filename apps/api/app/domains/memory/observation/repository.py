"""관찰의 CRUD·조회·집계·페이징."""

import enum
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
    ObservationStatus,
)


class ObservationDomain(enum.StrEnum):
    FOOD = "food"
    HEALTH = "health"
    EDUCATION = "education"
    ACTIVITY = "activity"
    ROUTINE = "routine"


_MODEL_BY_DOMAIN: dict[ObservationDomain, type[Any]] = {
    ObservationDomain.FOOD: ObservationFood,
    ObservationDomain.HEALTH: ObservationHealth,
    ObservationDomain.EDUCATION: ObservationEducation,
    ObservationDomain.ACTIVITY: ObservationActivity,
    ObservationDomain.ROUTINE: ObservationRoutine,
}


@dataclass(frozen=True)
class ObservationRecord:
    id: uuid.UUID
    domain: ObservationDomain
    child_id: uuid.UUID
    raw_text: str
    observed_range: Range[date]
    created_at: datetime
    fields: dict[str, Any]

    @property
    def observed_on(self) -> date:
        return self.observed_range.lower


@dataclass(frozen=True)
class ActiveObservationCounts:
    total_count: int
    period_count: int


@dataclass(frozen=True)
class ObservationCursor:
    """API 계층이 base64로 인코딩할 안정적인 페이지 위치."""

    observed_to_exclusive: date
    domain: ObservationDomain
    id: uuid.UUID


@dataclass(frozen=True)
class ObservationPage:
    items: list[ObservationRecord]
    total: int
    next_cursor: ObservationCursor | None


async def find_observation(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    observation_id: uuid.UUID,
) -> ObservationRecord | None:
    """상세 API의 기본 기록. 사용 제안과 교정 이력은 각 리포지터리에서 조회한다."""
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    row = await session.scalar(
        select(model).where(model.id == observation_id, model.child_id == child_id)
    )
    return _to_record(resolved, row) if row is not None else None


async def set_observation_status(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    observation_id: uuid.UUID,
    status: ObservationStatus,
) -> ObservationRecord | None:
    """교정의 once_only/wrong 결과만 반영한다. 이력·성향 재계산은 호출자가 묶는다."""
    status = ObservationStatus(status)
    if status not in {ObservationStatus.STAND_ALONE, ObservationStatus.INACTIVE}:
        raise ValueError("교정으로 설정할 수 없는 observation 상태다")
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    row = await session.scalar(
        update(model)
        .where(model.id == observation_id, model.child_id == child_id)
        .values(status=status)
        .returning(model)
    )
    return _to_record(resolved, row) if row is not None else None


async def count_active_observations(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> ActiveObservationCounts:
    """홈의 전체 active 수와 지정 기간 겹침 수. 종료일은 열린 경계다."""
    if period_start >= period_end:
        raise ValueError("집계 기간은 시작일보다 종료일이 늦어야 한다")
    row = (await session.execute(
        _COUNT_ACTIVE_SQL,
        {"child_id": child_id, "period_start": period_start, "period_end": period_end},
    )).one()
    return ActiveObservationCounts(total_count=row.total_count, period_count=row.period_count)


async def page_observations(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    domain: ObservationDomain | str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: ObservationStatus = ObservationStatus.ACTIVE,
    affinity_id: uuid.UUID | None = None,
    unused_in_suggestions: bool = False,
    cursor: ObservationCursor | None = None,
    limit: int = 20,
) -> ObservationPage:
    """5종 병합 목록: 기간 overlap, 상태/성향/근거사용 필터, 역순 커서."""
    if not 1 <= limit <= 100:
        raise ValueError("limit은 1~100이어야 한다")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from은 date_to보다 늦을 수 없다")
    resolved = ObservationDomain(domain) if domain is not None else None
    status = ObservationStatus(status)
    result = (await session.execute(
        _PAGE_SQL,
        {
            "child_id": child_id,
            "domain": resolved.value if resolved else None,
            "date_from": date_from,
            "date_to_exclusive": date_to + timedelta(days=1) if date_to else None,
            "status": status.value,
            "affinity_id": affinity_id,
            "unused_in_suggestions": unused_in_suggestions,
            "cursor_upper": cursor.observed_to_exclusive if cursor else None,
            "cursor_kind": f"observation_{cursor.domain.value}" if cursor else None,
            "cursor_id": cursor.id if cursor else None,
            "fetch_limit": limit + 1,
        },
    )).all()
    total = result[0].total if result else 0
    page_rows = [row for row in result if row.id is not None]
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]

    records: dict[tuple[str, uuid.UUID], ObservationRecord] = {}
    for kind in {row.kind for row in page_rows}:
        current_domain = ObservationDomain(kind.removeprefix("observation_"))
        model = _MODEL_BY_DOMAIN[current_domain]
        ids = [row.id for row in page_rows if row.kind == kind]
        for observation in (await session.scalars(
            select(model).where(model.child_id == child_id, model.id.in_(ids))
        )).all():
            records[(kind, observation.id)] = _to_record(current_domain, observation)
    items = [records[(row.kind, row.id)] for row in page_rows]
    last = page_rows[-1] if has_more else None
    next_cursor = (
        ObservationCursor(
            observed_to_exclusive=last.observed_to_exclusive,
            domain=ObservationDomain(last.kind.removeprefix("observation_")),
            id=last.id,
        )
        if last is not None else None
    )
    return ObservationPage(items=items, total=total, next_cursor=next_cursor)


def _to_record(domain: ObservationDomain, row: Any) -> ObservationRecord:
    fields = {
        column.key: getattr(row, column.key)
        for column in row.__table__.columns
        if column.key not in {"id", "child_id", "raw_text", "observed_range", "created_at"}
    }
    return ObservationRecord(
        id=row.id, domain=domain, child_id=row.child_id, raw_text=row.raw_text,
        observed_range=row.observed_range, created_at=row.created_at, fields=fields,
    )


# -- 필드 검증 --

_MANAGED_FIELDS = frozenset(
    {"id", "child_id", "raw_text", "observed_range", "created_at", "updated_at", "source_writer"}
)


def _allowed_fields(model: type) -> frozenset[str]:
    return frozenset(c.key for c in model.__table__.columns) - _MANAGED_FIELDS


def _validate_fields(model: type, keys: set[str] | frozenset[str]) -> None:
    bad = keys - _allowed_fields(model)
    if bad:
        raise ValueError(f"지원하지 않는 관찰 필드: {', '.join(sorted(bad))}")


def _validate_observed_range(observed_range: Range[date]) -> None:
    if observed_range.isempty or observed_range.upper is None:
        raise ValueError("observed_range는 비어 있거나 상한이 없는 범위일 수 없다")


# -- CRUD --


async def create_observation(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    source_writer: uuid.UUID,
    raw_text: str,
    observed_range: Range[date],
    fields: dict[str, Any],
) -> ObservationRecord:
    _validate_observed_range(observed_range)
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    _validate_fields(model, set(fields))
    row = model(
        child_id=child_id,
        source_writer=source_writer,
        raw_text=raw_text,
        observed_range=observed_range,
        **fields,
    )
    session.add(row)
    await session.flush()
    return _to_record(resolved, row)


async def query_observations(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    raw_text_query: str | None = None,
) -> list[ObservationRecord]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from은 date_to보다 늦을 수 없다")
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    stmt = select(model).where(model.child_id == child_id)
    # date_to는 inclusive — InMemoryStore(inmemory.py:79)의 observed_on <= date_to 와 일치
    dto_exclusive = date_to + timedelta(days=1) if date_to is not None else None
    if date_from is not None and dto_exclusive is not None:
        stmt = stmt.where(
            model.observed_range.op("&&")(func.daterange(date_from, dto_exclusive, "[)"))
        )
    elif date_from is not None:
        stmt = stmt.where(model.observed_range.op("&&")(func.daterange(date_from, None, "[)")))
    elif dto_exclusive is not None:
        stmt = stmt.where(model.observed_range.op("&&")(func.daterange(None, dto_exclusive, "[)")))
    if raw_text_query is not None:
        stmt = stmt.where(model.raw_text.contains(raw_text_query, autoescape=True))
    rows = (await session.scalars(stmt.order_by(model.created_at.desc()))).all()
    return [_to_record(resolved, row) for row in rows]


async def update_observation(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    observation_id: uuid.UUID,
    fields: dict[str, Any],
    clear: frozenset[str] = frozenset(),
) -> ObservationRecord | None:
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    _validate_fields(model, set(fields) | clear)
    values = {**fields, **{k: None for k in clear}}
    if not values:
        return await find_observation(
            session, domain=resolved, child_id=child_id, observation_id=observation_id
        )
    row = await session.scalar(
        update(model)
        .where(model.id == observation_id, model.child_id == child_id)
        .values(**values)
        .returning(model)
    )
    return _to_record(resolved, row) if row is not None else None


async def delete_observation(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    observation_id: uuid.UUID,
) -> bool:
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    result = await session.scalar(
        delete(model)
        .where(model.id == observation_id, model.child_id == child_id)
        .returning(model.id)
    )
    return result is not None


_COUNT_ACTIVE_SQL = text(
    Path(__file__).with_name("count_active_observations.sql").read_text(encoding="utf-8")
)
_PAGE_SQL = text(
    Path(__file__).with_name("page_observations.sql").read_text(encoding="utf-8")
)
