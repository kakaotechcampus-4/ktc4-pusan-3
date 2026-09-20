"""store 계약. 구현체는 이 Protocol만 만족하면 된다.

tool이 넘기는 값은 이미 확정된 값이다 — 날짜 표현은 tool 이 datetime_rules로 풀고,
child_id / source_writer 는 AgentContext가 채워서 준다. store는 저장과 조회만 한다.

id / created_at / uuid 생성은 store 책임이다.
이 파일은 DB 타입으로 바꾸는 지점에 해당한다.
tool은 파이썬 값(datetime/date/DateRange/int)을 그대로 넘기고,
ORM 어댑터가 여기서 변환한다 —
    DateRange  -> psycopg Range[date]  (DATERANGE)
    datetime   -> DateTime(timezone=True)
JSON 직렬화는 ToolResult를 만들 때만 한다. 저장 경로에서는 하지 않는다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from app.agents.common.datetime_rules import DateRange, EventWhen

# observation 5테이블. 도메인별 컬럼이 달라 payload로 받고 테이블만 이름으로 가름
ObservationDomain = str


@dataclass(frozen=True)
class ObservationRow:
    id: str
    domain: ObservationDomain
    raw_text: str
    created_at: datetime
    observed_on: date
    fields: dict[str, Any]  # 도메인별 컬럼. subject / symptom / activity ...

    def to_summary(self) -> dict[str, Any]:
        """조회 결과 요약. created_at · raw_text에 id를 붙인다."""
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class EventItemRow:
    item_id: str
    event_id: str
    item_name: str
    is_prepared: bool
    prepared_at: datetime | None = None  # 체크한 시각. 언제 정하는지는 tool 이 안다

    def to_summary(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_name": self.item_name,
            "is_prepared": self.is_prepared,
            "prepared_at": self.prepared_at.isoformat() if self.prepared_at else None,
        }


@dataclass(frozen=True)
class EventRow:
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    fields: dict[str, Any]  # event_type / category / status / created_by / expires_at

    def to_summary(self, items: list[EventItemRow] | None = None) -> dict[str, Any]:
        """조회 결과 요약.

        event 테이블에는 created_at 이 없다(ORM 확인). 시작 시각으로 대신한다.
        event_item 에는 조회 tool이 없어서 여기 같이 싣는다.
        """
        return {
            "id": self.id,
            "title": self.title,
            "starts_at": self.starts_at.isoformat(),
            "all_day": self.all_day,
            "items": [item.to_summary() for item in (items or [])],
        }


class MemoryStore(Protocol):
    """Memory Agent 가 쓰는 저장소. 전부 async."""

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
    ) -> ObservationRow: ...

    async def query_observations(
        self,
        *,
        domain: ObservationDomain,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        raw_text_query: str | None = None,
    ) -> list[ObservationRow]: ...

    async def get_observation(
        self, *, domain: ObservationDomain, observation_id: str
    ) -> ObservationRow | None: ...

    async def update_observation(
        self,
        *,
        domain: ObservationDomain,
        observation_id: str,
        fields: dict[str, Any],
        observed_on: date | None = None,
        clear: frozenset[str] = frozenset(),
    ) -> ObservationRow | None:
        """없으면 None.

        fields: 변경할 값
        값 비우기: clear(이름이 적힌 컬럼을 NULL로)
        같은 이름이 fields 와 clear 에 같이 오지 않게 tool 스키마가 막음.

        observed_on 이 주어지면 관찰 일자까지 바꾼다. 호출자가 observed_range 도 함께 넘긴다.
        subject 계열이 바뀌면 embedding 재계산이 필요하다.
        """
        ...

    async def delete_observation(
        self, *, domain: ObservationDomain, observation_id: str
    ) -> bool: ...

    # event
    async def create_event(
        self,
        *,
        child_id: UUID,
        title: str,
        starts_at: datetime,
        ends_at: datetime | None,
        all_day: bool,
        fields: dict[str, Any],
    ) -> EventRow: ...

    async def query_events(
        self,
        *,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        title_query: str | None = None,
    ) -> list[EventRow]: ...

    async def get_event(self, *, event_id: str) -> EventRow | None: ...

    async def update_event(
        self, *, event_id: str, fields: dict[str, Any], when: EventWhen | None = None
    ) -> EventRow | None:
        """없으면 None.

        fields: 변경할 값(비울 수 있는 컬럼이 없어 clear 인자 없음)
        종료 지우기: when.ends_at=None으로 들어옴(WhenPatch.drop_end)

        시간 구간은 when 하나로 받는다.
        시작·종료·all_day 는 함께 정해지는 값이라 한 덩어리로 오는 편이 안전하다
        (datetime_rules.resolve_when 이 셋을 같이 계산한다).
        """
        ...

    async def delete_event(self, *, event_id: str) -> bool:
        """연결된 event_item 도 함께 제거 (ON DELETE CASCADE)."""
        ...

    # event_item
    async def create_event_item(self, *, event_id: str, item_name: str) -> EventItemRow: ...

    async def get_event_item(self, *, item_id: str) -> EventItemRow | None:
        """없으면 None. 부모 일정과 현재 is_prepared 를 알아야 하는 tool 이 쓴다."""
        ...

    async def list_event_items(self, *, event_id: str) -> list[EventItemRow]: ...

    async def update_event_item(
        self, *, item_id: str, fields: dict[str, Any]
    ) -> EventItemRow | None:
        """없으면 None. fields에는 바꿀 것만 담긴다.

        prepared_at 은 tool 이 정해서 넘긴다. store 는 받은 값을 그대로 저장한다.
        """
        ...

    async def delete_event_item(self, *, item_id: str) -> bool: ...
