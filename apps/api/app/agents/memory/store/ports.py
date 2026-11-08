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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from app.agents.common.datetime_rules import DateRange
from app.rules.recurrence import repeat_label

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
        """조회 결과 요약. created_at · raw_text에 id를 붙인다.

        health 는 kind(symptom / medication)를 더 싣는다. 증상과 먹인 약을 한 번에 저장하면
        두 행의 raw_text 가 같아서, 이것 없이는 모델이 어느 행을 고칠지 못 가른다 (#551).
        """
        summary = {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "raw_text": self.raw_text,
        }
        if self.domain == "health":
            given = self.fields.get("medication_given") is not None
            summary["kind"] = "medication" if given else "symptom"
        return summary


@dataclass(frozen=True)
class EventItemRow:
    item_id: str
    event_id: str
    item_name: str
    is_prepared: bool
    prepared_at: datetime | None = None  # 체크한 시각. store 가 정한다 (set_event_item_prepared)

    def to_summary(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_name": self.item_name,
            "is_prepared": self.is_prepared,
            "prepared_at": self.prepared_at.isoformat() if self.prepared_at else None,
        }


@dataclass(frozen=True)
class SeriesRow:
    """요일 반복 묶음 (#738). 회차는 날짜마다 따로인 일정 행이고, 이 행이 규칙을 들고 있다."""

    id: str
    weekdays: tuple[int, ...]  # ISO 요일, 1=월 … 7=일
    starts_on: date  # 첫 회차 날짜
    until: date  # 마지막 날 (포함)


@dataclass(frozen=True)
class EventRow:
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    fields: dict[str, Any]  # event_type / category / created_by
    series: SeriesRow | None = None  # 반복 묶음의 회차이면 그 묶음

    def to_summary(self, items: list[EventItemRow] | None = None) -> dict[str, Any]:
        """조회 결과 요약.

        event 테이블에는 created_at 이 없다(ORM 확인). 시작 시각으로 대신한다.
        event_item 에는 조회 tool이 없어서 여기 같이 싣는다.
        반복 묶음의 회차이면 `series` 가 붙는다 — 모델이 어느 일정이 묶음인지 보고 범위를 정한다.
        """
        summary: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "starts_at": self.starts_at.isoformat(),
            "all_day": self.all_day,
            "items": [item.to_summary() for item in (items or [])],
        }
        if self.series is not None:
            summary["series"] = {
                "id": self.series.id,
                "label": repeat_label(self.series.weekdays, self.series.until),
            }
        return summary


def supports_series(store: object) -> bool:
    """이 store 가 반복 묶음(`SeriesRow` · `list_series_events` …)을 다룰 수 있는지.

    store 가 `supports_series = True` 를 달아야 열린다. 달지 않았으면 모르는 것으로 본다 —
    DB 어댑터가 묶음을 구현하기 전에 Agent 가 반복 일정 초안을 만들면, 제출할 곳이 없어 보호자가
    반복으로 말한 일정이 하나만 만들어진다. `is True` 로 보는 건 참처럼 보이는 값을 막는다.
    """
    return getattr(store, "supports_series", False) is True


@dataclass(frozen=True)
class DeleteCandidates:
    """삭제 제안의 한 종류(탭 하나) (#535).

    kind 는 `Saved` 의 ref 와 같은 값이다 — 관찰은 표 이름(`observation_food` …), 일정은 `event`.
    ids 는 모델이 그 run 에서 조회해 본 행(최대 DELETE_TAB_MAX_IDS 건), picked 는 그중 요청에 맞는
    것(기본 체크).
    truncated 는 조회가 잘려 더 있었는지 — 화면이 "더 있어요" 를 알린다.
    """

    kind: str
    truncated: bool
    ids: tuple[str, ...]
    picked: tuple[str, ...]


DELETE_KINDS = (
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "event",
)
"""제안에 담을 수 있는 종류 — 조회 tool 하나 = 탭 하나. 이 순서가 화면의 탭 순서다. DB 쪽도 같은
목록으로 한 번 더 막는다 (`domains/delete_proposal/repository.KINDS`)."""

DELETE_TAB_MAX_IDS = 40
"""탭 하나에 담을 수 있는 후보 수. 확정 요청의 id 상한(종류 수 × 이 값)이 이 값을 믿는다 — 넘게
담으면 "전부 체크" 확정이 후보 안의 id 인데도 거절된다. DB 쪽도 같은 값으로 한 번 더 막는다
(`domains/delete_proposal/repository.TAB_MAX_IDS`)."""


def check_candidates(tabs: Sequence[DeleteCandidates]) -> None:
    """제안 모양을 막는다 — 두 store 가 같은 규칙으로 거르게 여기 둔다. 어기면 ValueError.

    후보 id 가 정말 이 아이의 행인지는 여기서 보지 않는다. 지울 때 서버가 아이로 다시 거른다.
    """
    kinds = [tab.kind for tab in tabs]
    if not tabs or len(set(kinds)) != len(kinds) or not set(kinds) <= set(DELETE_KINDS):
        raise ValueError("후보가 없거나, 종류가 겹치거나, 담을 수 없는 종류다")
    for tab in tabs:
        if not tab.ids or len(set(tab.ids)) != len(tab.ids):
            raise ValueError("탭의 후보가 비었거나 같은 id 가 두 번 있다")
        if len(tab.ids) > DELETE_TAB_MAX_IDS:
            raise ValueError(f"탭의 후보는 {DELETE_TAB_MAX_IDS}건까지다")
        if len(set(tab.picked)) != len(tab.picked) or not set(tab.picked) <= set(tab.ids):
            raise ValueError("고른 id 가 겹치거나 후보 밖이다")


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
    ) -> list[ObservationRow]:
        """관찰한 날 오래된 순. 조회 tool 이 뒤에서 최근 40건을 자른다 (`result.recent`)."""
        ...

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

    async def delete_observation(self, *, domain: ObservationDomain, observation_id: str) -> bool:
        """soft delete. 행을 지우지 않고 status 를 deleted 로 바꾼다. 없거나 이미 지웠으면 False.

        지운 행은 query · get · update 어디에도 걸리지 않는다.
        """
        ...

    # event(create, update X)
    async def query_events(
        self,
        *,
        child_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        title_query: str | None = None,
    ) -> list[EventRow]:
        """시작 시각 오름차순. 조회 tool 이 오늘에 가까운 40건을 남긴다 (`result.nearest`)."""
        ...

    async def get_event(self, *, event_id: str) -> EventRow | None: ...

    async def delete_event(self, *, event_id: str) -> bool:
        """연결된 event_item 도 함께 제거 (ON DELETE CASCADE)."""
        ...

    # 반복 묶음 (#738)
    async def get_series(self, *, series_id: str) -> SeriesRow | None:
        """없으면(형식이 틀린 id 포함) None."""
        ...

    async def list_series_events(self, *, series_id: str, date_from: date) -> list[EventRow]:
        """`date_from` 이후(그날 포함) 회차를 날짜순으로. 묶음이 없으면 빈 목록."""
        ...

    # event_item(create X)
    async def get_event_item(self, *, item_id: str) -> EventItemRow | None:
        """없으면 None. 부모 일정과 현재 is_prepared 를 알아야 하는 tool 이 쓴다."""
        ...

    async def list_event_items(self, *, event_id: str) -> list[EventItemRow]: ...

    async def set_event_item_prepared(
        self, *, item_id: str, is_prepared: bool
    ) -> EventItemRow | None:
        """챙김 표시를 바꾼다. 없으면(형식이 틀린 id 포함) None.

        체크 시각(prepared_at)은 store 가 정한다 — 미체크 → 체크만 지금 시각, 이미 체크된 것을
        다시 체크하면 처음 시각을 그대로 둔다. 해제하면 None. 백엔드 PATCH 와 같은 규칙이다
        (`domains/schedule/repository.set_event_item_prepared`, #335).
        """
        ...

    async def delete_event_item(self, *, item_id: str) -> bool: ...

    # delete proposal (#535)
    async def propose_delete(
        self, *, parent_id: UUID, run_id: str, tabs: Sequence[DeleteCandidates]
    ) -> str:
        """지울 후보를 제안으로 남기고 id 를 돌려준다. 아무것도 지우지 않는다.

        보호자가 화면 버튼으로 확정하면 서버가 후보 안의 체크한 것만 지운다. 한 run 에 하나다 —
        같은 run 에서 다시 부르면 ValueError. 빈 후보 · 후보 밖의 picked 도 ValueError 다.
        """
        ...
