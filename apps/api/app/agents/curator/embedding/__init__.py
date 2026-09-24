"""Curator 임베딩 파트. 밖에서는 여기서만 import 한다.

관찰 subject 를 임베딩하고, 같은 아이 · 도메인 · polarity 의 Profile 에 연결하거나
새 candidate 를 만든다. 입구는 link_observations 하나다.
DB 구현은 CuratorStore Protocol 을 만족하면 된다.
계약 테스트: tests/unit/agents/curator/test_store.py
"""

from app.agents.curator.embedding.embedder import Embedder
from app.agents.curator.embedding.link_step import DEFAULT_THRESHOLD, LinkOutcome
from app.agents.curator.embedding.linker import LinkResult, link_observations
from app.agents.curator.embedding.ports import (
    CuratorStore,
    ObservationItem,
    ProfileItem,
)

__all__ = [
    "DEFAULT_THRESHOLD",
    "CuratorStore",
    "Embedder",
    "LinkOutcome",
    "LinkResult",
    "ObservationItem",
    "ProfileItem",
    "link_observations",
]
