"""tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id / source_writer)과 날짜 계산의 기준(now / timezone)을
한 곳에 모아 tool에 넘긴다. tool 인자에는 이 값들이 노출되지 않는다.
"""

from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.memory.store.ports import MemoryStore


@dataclass(frozen=True)
class AgentContext:
    child_id: UUID        # 인증 context 가 붙기 전까지는 호출자가 넣어준다
    source_writer: UUID   # 관찰을 기록한 보호자
    now: datetime         # timezone 이 붙은 현재 시각
    timezone: tzinfo      # 서비스 기준 timezone
    store: MemoryStore

    @property
    def today(self) -> date:
        """상대 날짜 해석의 기준일. 매번 now/tz 를 꺼내 쓰지 않게 한다."""
        return today_of(self.now, self.timezone)
