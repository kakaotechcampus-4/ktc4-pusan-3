"""tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id / source_writer)과 날짜 계산의 기준(now / timezone)을
한 곳에 모아 tool에 넘긴다. tool 인자에는 이 값들이 노출되지 않는다.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.memory.drafts import DraftBook
from app.agents.memory.store.ports import MemoryStore


@dataclass(frozen=True)
class AgentContext:
    child_id: UUID
    source_writer: UUID
    now: datetime
    timezone: tzinfo  # 서비스 기준 timezone
    store: MemoryStore
    # 일정 초안 버퍼(frozen이어도 안의 내용을 tool이 채움)
    drafts: DraftBook = field(default_factory=DraftBook)

    @property
    def today(self) -> date:
        """상대 날짜 해석의 기준일. 매번 now/tz 를 꺼내 쓰지 않게 한다."""
        return today_of(self.now, self.timezone)
