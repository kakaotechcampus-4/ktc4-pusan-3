"""Curator 임베딩 파트. 밖에서는 여기서만 import 한다.

관찰 subject 를 임베딩하고, 같은 아이 · 도메인 · polarity 의 Profile 중 같은 대상에 연결하거나
새 candidate 를 만든다. 같은 대상인지는 판정기(JevJudge)가 답한다. 입구는 link_observations 하나다.
DB 구현은 CuratorStore Protocol 을 만족하면 된다.
계약 테스트: tests/unit/agents/curator/test_store.py
"""

from app.agents.curator.embedding.embedder import Embedder
from app.agents.curator.embedding.jev import JevJudge
from app.agents.curator.embedding.judge import IdentityJudge, JudgeAnswer
from app.agents.curator.embedding.link_step import LinkOutcome
from app.agents.curator.embedding.linker import LinkResult, link_observations
from app.agents.curator.embedding.ports import (
    CuratorStore,
    ObservationItem,
    ProfileItem,
)

__all__ = [
    "CuratorStore",
    "Embedder",
    "IdentityJudge",
    "JevJudge",
    "JudgeAnswer",
    "LinkOutcome",
    "LinkResult",
    "ObservationItem",
    "ProfileItem",
    "link_observations",
]
