"""tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id / source_writer)과 날짜 계산의 기준(now / timezone)을
한 곳에 모아 tool에 넘긴다. tool 인자에는 이 값들이 노출되지 않는다.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.memory.drafts import DraftBook
from app.agents.memory.store.ports import DeleteCandidates, MemoryStore


@dataclass(frozen=True)
class FoundRows:
    """조회 한 번의 결과 id(조회 순서)와 MAX_ROWS 에서 잘렸는지."""

    ids: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class Proposal:
    """저장한 삭제 제안 — id 와 탭(후보 · 고른 것)."""

    id: str
    tabs: tuple[DeleteCandidates, ...]


@dataclass(frozen=True)
class AgentContext:
    child_id: UUID
    source_writer: UUID
    now: datetime
    timezone: tzinfo  # 서비스 기준 timezone
    store: MemoryStore
    # 일정 초안 버퍼(frozen이어도 안의 내용을 tool이 채움)
    drafts: DraftBook = field(default_factory=DraftBook)
    # 이번 run 에서 보호자가 실제로 한 말. run() 이 채운다 — 만드는 쪽은 넘기지 않는다.
    # tool 인자의 raw_text 는 모델이 옮겨 적은 글이라 "말한 그대로" 의 기준이 못 된다 (#551)
    utterance: str | None = None
    # 이 아이의 진행 중 복약 코스 이름. pipeline 이 기록 단계 전에 채운다. 코스를 못 읽었으면 비어
    # 있고, 그러면 먹인 약을 평소처럼 기록한다 — 투약이 두 번 남는 쪽이 기록이 사라지는 쪽보다 낫다
    registered_medicines: tuple[str, ...] = ()
    # 이 run 의 id. 삭제 제안이 run 하나에 하나인지를 store 가 이 값으로 막는다 (#535)
    run_id: str = ""
    # 종류(`observation_food` … · `event`)별 마지막 조회 결과 (#535). 조회 tool 이 채우고,
    # 같은 종류를 다시 조회하면 0건이어도 덮는다 — 좁힌 조회 밖의 기록이 후보로 남지 않게.
    # 삭제 제안의 후보이고 조회 카드의 탭이다. 모델이 고르지 않는다
    found: dict[str, "FoundRows"] = field(default_factory=dict)
    # 이 run 이 남긴 삭제 제안. 한 run 에 많아야 하나다. pipeline 이 commit 뒤 카드로 내보낸다
    proposals: list["Proposal"] = field(default_factory=list)

    @property
    def today(self) -> date:
        """상대 날짜 해석의 기준일. 매번 now/tz 를 꺼내 쓰지 않게 한다."""
        return today_of(self.now, self.timezone)
