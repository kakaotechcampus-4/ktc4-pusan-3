"""관찰의 CRUD·조회·집계·페이징."""

import enum
import uuid
from collections.abc import Collection
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
    for_update: bool = False,
) -> ObservationRecord | None:
    """상세 API의 기본 기록. 사용 제안과 교정 이력은 각 리포지터리에서 조회한다.

    for_update — 읽은 상태를 보고 바꿀 때 행을 잠가 같은 기록의 동시 요청을 순서대로 세운다.
    """
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    stmt = select(model).where(
        model.id == observation_id, model.child_id == child_id, _not_deleted(model)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = await session.scalar(stmt)
    return _to_record(resolved, row) if row is not None else None


async def set_observation_status(
    session: AsyncSession,
    *,
    domain: ObservationDomain | str,
    child_id: uuid.UUID,
    observation_id: uuid.UUID,
    status: ObservationStatus,
) -> ObservationRecord | None:
    """교정의 once_only/wrong 결과만 반영한다. 이력·성향 재계산은 호출자가 묶는다.

    지운(deleted) 행은 교정 대상이 아니라 None 이다.
    """
    status = ObservationStatus(status)
    if status not in {ObservationStatus.STAND_ALONE, ObservationStatus.INACTIVE}:
        raise ValueError("교정으로 설정할 수 없는 observation 상태다")
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    row = await session.scalar(
        update(model)
        .where(model.id == observation_id, model.child_id == child_id, _not_deleted(model))
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
    exclude_health: bool = False,
    statuses: Collection[ObservationStatus] = (ObservationStatus.ACTIVE,),
) -> ActiveObservationCounts:
    """홈의 전체 수와 지정 기간 겹침 수. 종료일은 열린 경계다.

    exclude_health — 첫 배포 범위에서 건강 기록을 세지 않는다 (#259).
    statuses — 셀 상태. 기본은 active 만이다. deleted 는 받지 않는다.
    """
    wanted = sorted({ObservationStatus(s).value for s in statuses})
    if not wanted or ObservationStatus.DELETED.value in wanted:
        raise ValueError("deleted 는 셀 수 없는 상태다")
    if period_start >= period_end:
        raise ValueError("집계 기간은 시작일보다 종료일이 늦어야 한다")
    row = (
        await session.execute(
            _COUNT_ACTIVE_SQL,
            {
                "child_id": child_id,
                "period_start": period_start,
                "period_end": period_end,
                "exclude_health": exclude_health,
                "statuses": wanted,
            },
        )
    ).one()
    return ActiveObservationCounts(total_count=row.total_count, period_count=row.period_count)


async def page_observations(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    domains: Collection[ObservationDomain | str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: ObservationStatus = ObservationStatus.ACTIVE,
    statuses: Collection[ObservationStatus] | None = None,
    affinity_id: uuid.UUID | None = None,
    unused_in_suggestions: bool = False,
    cursor: ObservationCursor | None = None,
    limit: int = 20,
) -> ObservationPage:
    """5종 병합 목록: 기간 overlap, 상태/성향/근거사용 필터, 역순 커서.

    domains 를 주면 그 표들만 합친다. growth Agent 처럼 한 분류가 두 표(education ·
    routine)를 읽는 경우가 있어 하나가 아니라 목록으로 받는다. 빈 목록은 받지 않는다 —
    "아무 표도 안 본다" 와 "전부 본다(None)" 가 섞이면 호출자 실수가 전체 조회로 새어 나간다.
    statuses 를 주면 status 대신 그 상태들을 합쳐서 본다. deleted 는 목록 필터로 열지 않는다.
    보호자에게 보이지 않는 상태다.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit은 1~100이어야 한다")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from은 date_to보다 늦을 수 없다")
    if domains is not None and not domains:
        raise ValueError("domains 는 비어 있을 수 없다")
    kinds = (
        sorted({f"observation_{ObservationDomain(d).value}" for d in domains})
        if domains is not None
        else None
    )
    wanted = (
        {ObservationStatus(s) for s in statuses}
        if statuses is not None
        else {ObservationStatus(status)}
    )
    if not wanted or ObservationStatus.DELETED in wanted:
        raise ValueError("deleted 는 목록으로 조회할 수 없는 상태다")
    result = (
        await session.execute(
            _PAGE_SQL,
            {
                "child_id": child_id,
                "kinds": kinds,
                "date_from": date_from,
                "date_to_exclusive": date_to + timedelta(days=1) if date_to else None,
                "statuses": sorted(s.value for s in wanted),
                "affinity_id": affinity_id,
                "unused_in_suggestions": unused_in_suggestions,
                "cursor_upper": cursor.observed_to_exclusive if cursor else None,
                "cursor_kind": f"observation_{cursor.domain.value}" if cursor else None,
                "cursor_id": cursor.id if cursor else None,
                "fetch_limit": limit + 1,
            },
        )
    ).all()
    total = result[0].total if result else 0
    page_rows = [row for row in result if row.id is not None]
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]

    records: dict[tuple[str, uuid.UUID], ObservationRecord] = {}
    for kind in {row.kind for row in page_rows}:
        current_domain = ObservationDomain(kind.removeprefix("observation_"))
        model = _MODEL_BY_DOMAIN[current_domain]
        ids = [row.id for row in page_rows if row.kind == kind]
        for observation in (
            await session.scalars(
                select(model).where(model.child_id == child_id, model.id.in_(ids))
            )
        ).all():
            records[(kind, observation.id)] = _to_record(current_domain, observation)
    items = [records[(row.kind, row.id)] for row in page_rows]
    last = page_rows[-1] if has_more else None
    next_cursor = (
        ObservationCursor(
            observed_to_exclusive=last.observed_to_exclusive,
            domain=ObservationDomain(last.kind.removeprefix("observation_")),
            id=last.id,
        )
        if last is not None
        else None
    )
    return ObservationPage(items=items, total=total, next_cursor=next_cursor)


# 기억(profile_affinity)으로 묶이는 표. routine · health 는 affinity_id 가 없다.
_PROMOTABLE_DOMAINS = (
    ObservationDomain.FOOD,
    ObservationDomain.EDUCATION,
    ObservationDomain.ACTIVITY,
)


async def list_active_refs_by_affinity(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    affinity_ids: Collection[uuid.UUID],
) -> dict[uuid.UUID, list[tuple[ObservationDomain, uuid.UUID]]]:
    """기억마다 묶인 active 관찰의 (도메인, id). 최근 관찰이 앞이다.

    기억 카드의 "지금까지 N번" 과 근거 목록이 쓴다. 집계에서 빠지는 stand_alone · inactive 는
    세지 않는다 — Curator 가 세는 것과 같은 기준이어야 카드 숫자가 상태와 어긋나지 않는다.
    """
    refs: dict[uuid.UUID, list[tuple[date, ObservationDomain, uuid.UUID]]] = {}
    if not affinity_ids:
        return {}
    for domain in _PROMOTABLE_DOMAINS:
        model = _MODEL_BY_DOMAIN[domain]
        rows = await session.execute(
            select(model.affinity_id, model.id, func.upper(model.observed_range)).where(
                model.child_id == child_id,
                model.affinity_id.in_(affinity_ids),
                model.status == ObservationStatus.ACTIVE,
            )
        )
        for affinity_id, observation_id, upper in rows:
            refs.setdefault(affinity_id, []).append((upper, domain, observation_id))
    return {
        affinity_id: [(domain, oid) for _, domain, oid in sorted(found, reverse=True)]
        for affinity_id, found in refs.items()
    }


def _to_record(domain: ObservationDomain, row: Any) -> ObservationRecord:
    fields = {
        column.key: getattr(row, column.key)
        for column in row.__table__.columns
        if column.key not in {"id", "child_id", "raw_text", "observed_range", "created_at"}
    }
    return ObservationRecord(
        id=row.id,
        domain=domain,
        child_id=row.child_id,
        raw_text=row.raw_text,
        observed_range=row.observed_range,
        created_at=row.created_at,
        fields=fields,
    )


def _not_deleted(model: type[Any]) -> Any:
    """지운 행은 조회·수정·교정 어디에도 걸리지 않는다."""
    return model.status != ObservationStatus.DELETED


# -- 필드 검증 --

# status 는 set_observation_status(교정)와 delete_observation(삭제)만 쓴다
_MANAGED_FIELDS = frozenset(
    {
        "id",
        "child_id",
        "raw_text",
        "observed_range",
        "created_at",
        "updated_at",
        "source_writer",
        "status",
    }
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
    stmt = select(model).where(model.child_id == child_id, _not_deleted(model))
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
        .where(model.id == observation_id, model.child_id == child_id, _not_deleted(model))
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
    """행과 원문은 남기고 status 만 deleted 로 바꾼다.

    이미 지운 행은 대상이 아니라 False 다.
    soft delete 라 FK CASCADE 가 발동하지 않으므로, Curator 보류 기록(observation_link_hold)을
    코드에서 직접 지운다.
    """
    resolved = ObservationDomain(domain)
    model = _MODEL_BY_DOMAIN[resolved]
    result = await session.scalar(
        update(model)
        .where(model.id == observation_id, model.child_id == child_id, _not_deleted(model))
        .values(status=ObservationStatus.DELETED)
        .returning(model.id)
    )
    if result is not None:
        # Curator 보류 기록 정리 — soft delete 에서 CASCADE 가 안 먹히므로 코드가 지운다
        from app.domains.memory.observation.models import ObservationLinkHold

        await session.execute(
            delete(ObservationLinkHold).where(
                ObservationLinkHold.domain == resolved.value,
                ObservationLinkHold.observation_id == observation_id,
            )
        )
    return result is not None


_COUNT_ACTIVE_SQL = text(
    Path(__file__).with_name("count_active_observations.sql").read_text(encoding="utf-8")
)
_PAGE_SQL = text(Path(__file__).with_name("page_observations.sql").read_text(encoding="utf-8"))
