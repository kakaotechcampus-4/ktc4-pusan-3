"""요청 기반 일반 추천을 여는 Agent 표는 두 층에 따로 있다 — 같은 값인지 대조한다."""

from app.agents.common.suggestion import REQUESTED_AGENTS
from app.domains.suggestion.repository import _REQUESTED_AGENTS


def test_두_층의_요청_기반_Agent_표가_같다():
    """agents 와 domains 는 서로 import 하지 않는다. 표가 어긋나면 Agent 가 낸 요청 기반 추천을
    저장 검사가 거절하거나, 저장 검사만 열려 있어 문서 근거 없는 추천이 들어간다."""
    assert _REQUESTED_AGENTS == REQUESTED_AGENTS
