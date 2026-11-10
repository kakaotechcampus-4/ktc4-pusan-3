"""MemoryStore Protocol 의 SQLAlchemy 구현.

세션은 밖에서 받는다 (DbCuratorStore 패턴과 동일).
트랜잭션 경계는 호출하는 쪽이 정한다.

InMemoryStore 와 동일한 fields 매핑을 유지한다 -- Memory Agent 도구가
row.fields["subject"], row.fields["status"] 등으로 읽기 때문이다.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.datetime_rules import DateRange
from app.agents.memory.store.ports import (
    DeleteCandidates,
    EventItemRow,
    EventRow,
    NoticeItemRow,
    NoticeRow,
    ObservationDomain,
    ObservationRow,
    SeriesRow,
    check_candidates,
)
from app.domains.delete_proposal.repository import PROPOSAL_TTL, ProposalTab, add_proposal
from app.domains.memory.observation.models import (
    GONE_STATUSES,
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationLinkHold,
    ObservationRoutine,
    ObservationStatus,
    Promotable,
)
from app.domains.memory.profile.service import lock_child_memory, refresh_profile
from app.domains.notice import repository as notice_repository
from app.domains.notice.models import Notice, NoticeDocKind, NoticeItem
from app.domains.schedule import repository as schedule_repository
from app.domains.schedule import series as series_repository
from app.domains.schedule.models import Event, EventItem, EventSeries

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

# 관찰을 고칠 때 Curator 집계에 영향을 주는 칸 (#257)
# 바뀌면 벡터 · Profile 연결을 비운다 — 다음 Curator 가 다시 임베딩 · 연결한다
_RELINK_KEYS = frozenset({"subject", "polarity"})
# 바뀌면 원래 Profile 의 last_observed_on 과 상태를 다시 계산한다
_RECOMPUTE_KEYS = _RELINK_KEYS | {"observed_range", "strong_signals"}


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
# ObservationRow 의 최상위 속성 + Agent 에 넘길 필요 없는 내부 컬럼.
_ROW_EXCLUDE_KEYS = frozenset(
    {
        "id",
        "raw_text",
        "created_at",
        "updated_at",  # ORM 내부 타임스탬프
        "embedding",  # 1536차원 벡터 — Curator 전용, Agent 에 불필요
        "affinity_id",  # Curator 가 채우는 Profile 연결 — Agent 에 불필요
        "strong_signals",  # Promotable 내부 — Agent 에 불필요
    }
)


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
        if key in _ROW_EXCLUDE_KEYS:
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


def _series_row(orm: EventSeries) -> SeriesRow:
    return SeriesRow(
        id=str(orm.id), weekdays=tuple(orm.weekdays), starts_on=orm.starts_on, until=orm.until
    )


def _event_row(orm: Event, series: EventSeries | None = None) -> EventRow:
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
        series=_series_row(series) if series is not None else None,
    )


def _event_item_row(orm: EventItem) -> EventItemRow:
    return EventItemRow(
        item_id=str(orm.item_id),
        event_id=str(orm.event_id),
        item_name=orm.item_name,
        is_prepared=orm.is_prepared,
        prepared_at=orm.prepared_at,
    )


def _notice_row(notice: Notice, items: Sequence[NoticeItem]) -> NoticeRow:
    """ORM → 포트 행. 식별자 · 저장 메타는 여기서 떨어진다 (#832)."""
    return NoticeRow(
        doc_kind=notice.doc_kind.value,
        input_format=notice.input_format.value,
        audience_scope=notice.audience_scope.value,
        issued_on=notice.issued_on,
        received_at=notice.received_at,
        title=notice.title,
        raw_text=notice.raw_text,
        relation=notice.relation.value if notice.relation else None,
        items=tuple(
            NoticeItemRow(
                handling=i.handling.value,
                label=i.label,
                title=i.title,
                target_scope=i.target_scope.value,
                starts_at=i.starts_at,
                ends_at=i.ends_at,
                all_day=i.all_day,
                due_at=i.due_at,
                due_all_day=i.due_all_day,
                details=tuple((d["name"], d["value"]) for d in i.details),
                supplies=tuple(i.supplies),
                source_quote=i.source_quote,
                done_at=i.done_at,
            )
            for i in items
        ),
    )


class DbMemoryStore:
    """MemoryStore Protocol 구현. 세션 하나가 run 하나를 의미한다."""

    supports_series = True  # 반복 묶음을 읽는다 (#738 · `ports.supports_series()`)

    def __init__(self, session: AsyncSession, *, child_id: UUID, today: date | None = None) -> None:
        self._session = session
        self._child_id = child_id
        self._fixed_today = today
        self.wrote = False
        """create / update / delete 가 DB 에 실제로 쓴 적이 있으면 True.
        runner 가 commit 표시를 세울지 판단하는 데 쓴다."""
        self.created: list[tuple[str, UUID]] = []
        """이 run 에서 새로 만든 관찰 (도메인, id) — 만든 순서대로. 고치기 · 지우기는 넣지 않는다.
        runner 가 commit 뒤에 이 행들을 읽어 화면에 `saved` 로 보낸다 (#471)."""
        self._stale_profiles: set[UUID] = set()
        """고치기 · 지우기로 다시 계산할 기억. `flush_profiles` 가 commit 직전에 비운다 (#717)."""
        self.proposal_id: UUID | None = None
        # 카드의 expires_at — 러너가 SSE delete_proposal 에 싣는다 (#535)
        self.proposal_expires_at: datetime | None = None
        """이 run 이 남긴 삭제 제안 (#535). runner 가 commit 뒤에 후보 행을 읽어 화면에 보낸다."""

    def _today(self) -> date:
        """Profile 재계산 기준일. 안 넘기면 서비스 시간대(KST)의 오늘."""
        return self._fixed_today or datetime.now(_KST).date()

    async def _lock_child(self) -> None:
        """관찰을 고치거나 지우기 **전에** 아이 잠금을 잡는다 (#636).

        Curator · 하루 배치와 같은 잠금이다. 순서가 아이 잠금 → 관찰 행 → 기억 행으로 같아야
        교착이 없다 — run 트랜잭션은 고친 관찰 · 기억의 행 잠금을 커밋까지 쥐는데, 그사이
        Curator 가 아이 잠금을 쥔 채 같은 행을 기다리면 둘이 서로를 기다린다.
        연결 안 된 관찰을 고칠 때도 잡는다 — Curator 가 바로 그 관찰을 연결하려 할 수 있다.
        새 관찰 저장은 잡지 않는다 — 커밋 전에는 Curator 에게 보이지 않아 겹치지 않는다.
        같은 트랜잭션은 다시 잡아도 기다리지 않는다.
        """
        started = monotonic()
        await lock_child_memory(self._session, self._child_id)
        # 기다린 시간 — 직전 입력의 Curator 가 쥐고 있으면 그만큼 20초 예산에서 빠진다 (#636).
        # 같은 run 의 두 번째 수정부터는 이미 쥐고 있어 0 이다
        logger.info(
            "Memory 아이 잠금 child_id=%s wait_ms=%d",
            self._child_id,
            (monotonic() - started) * 1000,
        )

    def _mark_profile_stale(self, affinity_id: UUID | None) -> None:
        """다시 계산할 기억을 적어만 둔다 — 잠그는 건 `flush_profiles` 가 commit 직전에 한다 (#717).

        고칠 때마다 바로 계산하면 "관찰 X → 기억 A → 관찰 Y" 로 잠금 순서가 섞여, 같은 기억의
        다른 관찰을 정정하는 쪽("관찰 Y → 기억 A")과 서로를 기다린다. 기억을 맨 끝에 몰아서 잠그면
        run 도 "관찰 → 기억" 이라 순환이 없다. Curator 가 `recompute_after_linking` 으로 기억을
        마지막에 몰아 계산하는 것과 같은 순서다.
        """
        if affinity_id is not None:
            self._stale_profiles.add(affinity_id)

    async def flush_profiles(self) -> None:
        """모아 둔 기억을 id 순으로 한 번씩 다시 계산한다. 러너가 `session.commit()` 직전에 부른다.

        기억 행 잠금은 커밋까지 쥐므로 관찰 수정이 다 끝난 뒤에 잡는다. 같은 아이의 다른 run ·
        Curator · 배치와는 기억을 잠그는 순서가 아니라 공통 아이 잠금(`_lock_child`)으로 차례가
        정해진다. id 순으로 도는 건 결과를 재현 가능하게 하려는 것뿐이다. 도메인 Agent 는 commit
        뒤에 검색하므로 "저장이 검색보다 먼저"(CLAUDE.md §4)는 그대로다. 비우고 나가서 다시 불려도
        두 번 계산하지 않는다.

        run 안에서 상태가 내려갔다 돌아와도 strength 보정은 최종 관찰 상태로 한 번만 한다 — 중간
        상태는 커밋되지 않아 아무도 보지 못한다 (#745 리뷰).
        """
        stale = sorted(self._stale_profiles)
        self._stale_profiles.clear()
        for affinity_id in stale:
            await refresh_profile(self._session, profile_id=affinity_id, today=self._today())

    async def _clear_link_hold(self, domain: ObservationDomain, observation_id: UUID) -> None:
        await self._session.execute(
            delete(ObservationLinkHold).where(
                ObservationLinkHold.domain == domain,
                ObservationLinkHold.observation_id == observation_id,
            )
        )

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
        if isinstance(col_fields.get("linked_symptom_id"), str):
            # tool 은 앞서 만든 증상 행의 문자열 id 를 그대로 넘긴다 (#551)
            col_fields["linked_symptom_id"] = UUID(col_fields["linked_symptom_id"])
        orm = model(
            child_id=child_id,
            source_writer=source_writer,
            raw_text=raw_text,
            observed_range=_daterange_to_psycopg(observed_range),
            **col_fields,
        )
        self._session.add(orm)
        await self._session.flush()
        self.wrote = True
        self.created.append((domain, orm.id))
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
            model.status.notin_(GONE_STATUSES),
        )
        if date_from is not None:
            stmt = stmt.where(model.observed_range.op("&&")(func.daterange(date_from, None, "[)")))
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
                model.status.notin_(GONE_STATUSES),
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
        await self._lock_child()
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
        # Curator 집계에 영향을 주는 칸이 실제로 바뀌는지 고치기 전 값과 비교한다
        affinity_id: UUID | None = None
        changed: set[str] = set()
        if issubclass(model, Promotable):
            watched = sorted(_RECOMPUTE_KEYS & values.keys())
            before = (
                await self._session.execute(
                    select(model.affinity_id, *(getattr(model, k) for k in watched)).where(
                        model.id == uid,
                        model.child_id == self._child_id,
                        model.status.notin_(GONE_STATUSES),
                    )
                )
            ).first()
            if before is not None:
                affinity_id = before[0]
                changed = {k for k, old in zip(watched, before[1:]) if values[k] != old}
            if changed & _RELINK_KEYS:
                values["embedding"] = None
                values["affinity_id"] = None
        orm = await self._session.scalar(
            update(model)
            .where(
                model.id == uid,
                model.child_id == self._child_id,
                model.status.notin_(GONE_STATUSES),
            )
            .values(**values)
            .returning(model)
        )
        if orm is None:
            return None
        self.wrote = True
        if changed & _RELINK_KEYS:
            # 옛 subject · polarity 로 쌓인 보류 기록은 다시 연결할 때 쓸모가 없다
            await self._clear_link_hold(domain, uid)
        if changed:
            self._mark_profile_stale(affinity_id)
        return _row_from_orm(domain, orm)

    async def delete_observation(self, *, domain: ObservationDomain, observation_id: str) -> bool:
        uid = _parse_uuid(observation_id)
        if uid is None:
            return False
        await self._lock_child()
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
        if result is None:
            return False
        self.wrote = True
        await self._clear_link_hold(domain, uid)
        if issubclass(model, Promotable):
            # 지운 관찰 몫을 원래 Profile 에서 다시 계산한다 (#257)
            affinity_id = await self._session.scalar(
                select(model.affinity_id).where(model.id == uid)
            )
            self._mark_profile_stale(affinity_id)
        return True

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
        return await self._event_rows(rows)

    # ── 기관 공지 (#832) ────────────────────────────────────────

    async def query_notices(
        self,
        *,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        text_query: str | None = None,
        doc_kind: str | None = None,
        limit: int,
    ) -> list[NoticeRow]:
        assert child_id == self._child_id, "child_id mismatch"
        found = await notice_repository.find_notices(
            self._session,
            child_id=child_id,
            date_from=date_from,
            date_to=date_to,
            text_query=text_query,
            doc_kind=NoticeDocKind(doc_kind) if doc_kind else None,
            limit=limit,
            tz=_KST,
        )
        return [_notice_row(notice, items) for notice, items in found]

    async def _event_rows(self, rows: Any) -> list[EventRow]:
        series = await series_repository.series_by_id(
            self._session, series_ids={r.series_id for r in rows}
        )
        return [_event_row(r, series.get(r.series_id)) for r in rows]

    async def get_event(self, *, event_id: str) -> EventRow | None:
        uid = _parse_uuid(event_id)
        if uid is None:
            return None
        orm = await self._session.scalar(
            select(Event).where(Event.id == uid, Event.child_id == self._child_id)
        )
        return (await self._event_rows([orm]))[0] if orm is not None else None

    # ── 반복 묶음 (#738) ────────────────────────────────────────

    async def get_series(self, *, series_id: str) -> SeriesRow | None:
        uid = _parse_uuid(series_id)
        if uid is None:
            return None
        orm = await series_repository.find_series(
            self._session, child_id=self._child_id, series_id=uid
        )
        return _series_row(orm) if orm is not None else None

    async def list_series_events(self, *, series_id: str, date_from: date) -> list[EventRow]:
        uid = _parse_uuid(series_id)
        if uid is None:
            return []
        rows = await series_repository.list_series_events(
            self._session, child_id=self._child_id, series_id=uid, date_from=date_from
        )
        return await self._event_rows(rows)

    async def delete_event(self, *, event_id: str) -> bool:
        uid = _parse_uuid(event_id)
        if uid is None:
            return False
        # 🚨 DELETE … RETURNING 으로 실제로 지웠는지 본다. 읽고 나서 ORM 으로 지우면, 같은 일정을
        #    다른 트랜잭션이 먼저 지웠을 때 0행 삭제가 경고로만 끝나 True 가 된다 — 버튼 확정이
        #    지운 건수를 두 번 센다 (#535). 준비물 · 알림 · 추천 연결은 DB 의 ON DELETE CASCADE 다
        deleted = await schedule_repository.delete_event(
            self._session, child_id=self._child_id, event_id=uid
        )
        if deleted:
            self.wrote = True
        return deleted

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

    async def set_event_item_prepared(
        self, *, item_id: str, is_prepared: bool
    ) -> EventItemRow | None:
        uid = _parse_uuid(item_id)
        if uid is None:
            return None
        # 시각 규칙은 백엔드 PATCH 와 같은 함수 하나에 둔다 — 이전 값을 UPDATE 안에서 본다
        orm = await schedule_repository.set_event_item_prepared(
            self._session, child_id=self._child_id, item_id=uid, is_prepared=is_prepared
        )
        if orm is None:
            return None
        self.wrote = True
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
        self.wrote = True
        return True

    # ── delete proposal (#535) ──────────────────────────────────

    async def propose_delete(
        self, *, parent_id: UUID, run_id: str, tabs: Sequence[DeleteCandidates]
    ) -> str:
        if self.proposal_id is not None:
            raise ValueError("한 run 에 제안은 하나다")
        check_candidates(tabs)
        now = datetime.now(UTC)
        self.proposal_id = await add_proposal(
            self._session,
            parent_id=parent_id,
            child_id=self._child_id,
            run_id=run_id,
            tabs=[ProposalTab(tab.kind, tab.truncated, tab.ids, tab.picked) for tab in tabs],
            now=now,
        )
        self.proposal_expires_at = now + PROPOSAL_TTL
        # 제안도 commit 해야 남는 쓰기다 — 뒤에서 터져도 run 을 done 으로 닫아 키를 지킨다
        self.wrote = True
        return str(self.proposal_id)
