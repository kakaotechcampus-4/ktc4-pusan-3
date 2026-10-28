"""도메인 Agent 의 기억 포트가 읽는 `profile_affinity` 어댑터.

Curator 가 관찰을 모아 만든 관심(기억 화면의 "공룡 좋아함")을 Food · Activity · Growth 가
근거로 읽게 한다. Memory store(`DbMemoryStore`)에는 이 테이블이 없어 `memory_bridge` 가
이 어댑터를 따로 받는다.
api 가 만들어(`agent_ports.affinity_reader()`) 러너와 05 가 entrypoint 로 넘긴다.
포트 모양(`AffinityRow`)은 agents 가 정한 계약이라 여기서 import 한다(apps/api/CLAUDE.md "agents 가
정한 저장 계약").

- 기억 화면(`list_affinities`)과 같은 행이다 — `archived` 만 뺀다. 극성이 빈 candidate 도 싣는다.
  Food 되묻기(`pick_followup`)가 그 행을 본다.
- 시간 경과 archived 는 여기서 판정하지 않는다. 기억 다리가 그 run 의 기준일로 맞춘다.
  티어 · 6개월 낡음도 거르지 않는다 — `rank_evidence` · `build()` 가 판정한다.
- 호출마다 짧은 세션을 연다 (#257). 읽기 실패는 그대로 올린다 — 관찰 읽기와 같다.
- merge_key · 상태 · 극성 · 세기 · 마지막 관찰일만 옮긴다. embedding 은 싣지 않는다.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common.evidence import AffinityRow
from app.domains.memory.profile.models import MemoryDomain
from app.domains.memory.profile.repository import list_affinities

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DbAffinityReader:
    """Food · Activity · Growth 기억 포트의 `affinities()` 가 이 어댑터로 읽는다."""

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def affinities(self, *, child_id: UUID, domains: Sequence[str]) -> list[AffinityRow]:
        rows = []
        async with self._open() as session:
            for domain in dict.fromkeys(domains):
                rows += await list_affinities(
                    session, child_id=child_id, domain=MemoryDomain(domain)
                )
        return [
            AffinityRow(
                id=row.id,
                merge_key=row.merge_key,
                state=row.state.value,
                polarity=row.polarity,
                strength=row.strength,
                last_observed_on=row.last_observed_on,
            )
            for row in rows
        ]
