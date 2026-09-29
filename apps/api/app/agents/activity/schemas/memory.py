"""놀이 기억 검색 인자.

observation_activity + profile_affinity(domain=activity)를 rank_evidence로 줄 세워 돌려준다.
기간은 받지 않는다 — 관찰은 최근 14일(티어 3), affinity는 무기한이다 (common/evidence.py).
"""

from typing import Annotated

from pydantic import Field

from app.agents.activity.schemas.common import ToolArgs


class SearchActivityMemoryArgs(ToolArgs):
    keywords: Annotated[
        list[str],
        Field(
            default_factory=list,
            max_length=5,
            description="찾을 놀이·관심 이름. 예: 블록, 모래놀이. 비우면 최근 기록과 관심 전체",
        ),
    ]
