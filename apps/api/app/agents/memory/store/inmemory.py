"""인메모리 store. domains 에 ORM 모델이 생기기 전까지 tool 테스트에 이용한다.
재현 가능한 테스트를 위해 id는 도메인별 순번(observation_food-1)으로 만든다.
"""

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.agents.common.datetime_rules import DateRange
from app.agents.memory.store.ports import (
    EventItemRow,
    EventRow,
    ObservationDomain,
    ObservationRow,
    ReminderRow,
)


class InMemoryStore:
    """MemoryStore Protocol 구현. 한 인스턴스가 한 테스트/한 run의 저장소를 의미한다."""

    def __init__(self, now: datetime | None = None) -> None:
        # created_at 을 고정할 수 있어야 조회 결과 검증 가능
        self._now = now or datetime.now(timezone.utc)
        self._observations: dict[str, ObservationRow] = {}
        self._events: dict[str, EventRow] = {}
        self._items: dict[str, EventItemRow] = {}
        self._reminders: dict[str, ReminderRow] = {}
        self._counters: dict[str, int] = {}

    def _next_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]}"

    # observation
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
        row = ObservationRow(
            id=self._next_id(f"observation_{domain}"),
            domain=domain,
            raw_text=raw_text,
            created_at=self._now,
            observed_on=observed_on,
            fields={
                **fields,
                "child_id": str(child_id),
                "source_writer": str(source_writer),
                "observed_range": observed_range,
                "status": "active",
            },
        )
        self._observations[row.id] = row
        return row

    async def query_observations(
        self,
        *,
        domain: ObservationDomain,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        raw_text_query: str | None = None,
    ) -> list[ObservationRow]:
        rows = [
            row
            for row in self._observations.values()
            if row.domain == domain and row.fields.get("child_id") == str(child_id)
        ]
        if date_from is not None:
            rows = [row for row in rows if row.observed_on >= date_from]
        if date_to is not None:
            rows = [row for row in rows if row.observed_on <= date_to]
        if raw_text_query:
            rows = [row for row in rows if raw_text_query in row.raw_text]
        return sorted(rows, key=lambda row: (row.observed_on, row.id))

    async def get_observation(
        self, *, domain: ObservationDomain, observation_id: str
    ) -> ObservationRow | None:
        row = self._observations.get(observation_id)
        return row if row is not None and row.domain == domain else None

    async def update_observation(
        self,
        *,
        domain: ObservationDomain,
        observation_id: str,
        fields: dict[str, Any],
        observed_on: date | None = None,
    ) -> ObservationRow | None:
        row = await self.get_observation(domain=domain, observation_id=observation_id)
        if row is None:
            return None

        changes = {key: value for key, value in fields.items() if value is not None}
        updated = ObservationRow(
            id=row.id,
            domain=row.domain,
            raw_text=row.raw_text,
            created_at=row.created_at,
            observed_on=observed_on or row.observed_on,
            fields={**row.fields, **changes},
        )
        self._observations[row.id] = updated
        return updated

    async def delete_observation(
        self, *, domain: ObservationDomain, observation_id: str
    ) -> bool:
        row = await self.get_observation(domain=domain, observation_id=observation_id)
        if row is None:
            return False
        del self._observations[observation_id]
        return True

    # ── event ───────────────────────────────────────────────────
    async def create_event(
        self,
        *,
        child_id: UUID,
        title: str,
        starts_at: datetime,
        ends_at: datetime | None,
        all_day: bool,
        fields: dict[str, Any],
    ) -> EventRow:
        row = EventRow(
            id=self._next_id("event"),
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            fields={**fields, "child_id": str(child_id)},
        )
        self._events[row.id] = row
        return row

    async def query_events(
        self,
        *,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        title_query: str | None = None,
    ) -> list[EventRow]:
        rows = [
            row for row in self._events.values() if row.fields.get("child_id") == str(child_id)
        ]
        # 날짜 조건은 starts_at 기준
        if date_from is not None:
            rows = [row for row in rows if row.starts_at.date() >= date_from]
        if date_to is not None:
            rows = [row for row in rows if row.starts_at.date() <= date_to]
        if title_query:
            rows = [row for row in rows if title_query in row.title]
        return sorted(rows, key=lambda row: (row.starts_at, row.id))

    async def get_event(self, *, event_id: str) -> EventRow | None:
        return self._events.get(event_id)

    async def update_event(self, *, event_id: str, fields: dict[str, Any]) -> EventRow | None:
        row = self._events.get(event_id)
        if row is None:
            return None

        changes = {key: value for key, value in fields.items() if value is not None}
        updated = EventRow(
            id=row.id,
            title=changes.pop("title", row.title),
            starts_at=changes.pop("starts_at", row.starts_at),
            ends_at=changes.pop("ends_at", row.ends_at),
            all_day=changes.pop("all_day", row.all_day),
            fields={**row.fields, **changes},
        )
        self._events[row.id] = updated
        return updated

    async def delete_event(self, *, event_id: str) -> bool:
        if event_id not in self._events:
            return False
        del self._events[event_id]
        # ON DELETE CASCADE(일정이 사라지면 준비물과 알림도 같이 정리)
        self._items = {
            key: item for key, item in self._items.items() if item.event_id != event_id
        }
        self._reminders = {
            key: reminder
            for key, reminder in self._reminders.items()
            if reminder.event_id != event_id
        }
        return True

    # event_item
    async def create_event_item(self, *, event_id: str, item_name: str) -> EventItemRow:
        row = EventItemRow(
            item_id=self._next_id("event_item"),
            event_id=event_id,
            item_name=item_name,
            is_prepared=False,
        )
        self._items[row.item_id] = row
        return row

    async def list_event_items(self, *, event_id: str) -> list[EventItemRow]:
        rows = [item for item in self._items.values() if item.event_id == event_id]
        return sorted(rows, key=lambda item: item.item_id)

    async def update_event_item(
        self, *, item_id: str, fields: dict[str, Any]
    ) -> EventItemRow | None:
        row = self._items.get(item_id)
        if row is None:
            return None

        changes = {key: value for key, value in fields.items() if value is not None}
        updated = EventItemRow(
            item_id=row.item_id,
            event_id=row.event_id,
            item_name=changes.get("item_name", row.item_name),
            is_prepared=changes.get("is_prepared", row.is_prepared),
        )
        self._items[row.item_id] = updated
        return updated

    async def delete_event_item(self, *, item_id: str) -> bool:
        return self._items.pop(item_id, None) is not None

    # reminder
    async def create_reminder(self, *, event_id: str, remind_at: datetime) -> ReminderRow:
        row = ReminderRow(
            id=self._next_id("reminder"), event_id=event_id, remind_at=remind_at
        )
        self._reminders[row.id] = row
        return row

    async def get_reminder(self, *, reminder_id: str) -> ReminderRow | None:
        return self._reminders.get(reminder_id)

    async def list_reminders(self, *, event_id: str) -> list[ReminderRow]:
        rows = [item for item in self._reminders.values() if item.event_id == event_id]
        return sorted(rows, key=lambda item: (item.remind_at, item.id))

    async def update_reminder(
        self, *, reminder_id: str, remind_at: datetime
    ) -> ReminderRow | None:
        row = self._reminders.get(reminder_id)
        if row is None:
            return None
        updated = ReminderRow(id=row.id, event_id=row.event_id, remind_at=remind_at)
        self._reminders[row.id] = updated
        return updated

    async def delete_reminder(self, *, reminder_id: str) -> bool:
        return self._reminders.pop(reminder_id, None) is not None
