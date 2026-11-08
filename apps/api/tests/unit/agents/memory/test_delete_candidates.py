"""삭제 제안 후보 모양 검사 — app/agents/memory/store/ports.check_candidates (#535).

탭 하나의 후보는 DELETE_TAB_MAX_IDS 건까지다. 확정 요청의 id 상한(종류 수 × 이 값)이 이 값을 믿어서,
넘게 담기면 "전부 체크" 확정이 후보 안의 id 인데도 거절된다.
"""

import pytest

from app.agents.memory.store.ports import DELETE_TAB_MAX_IDS, DeleteCandidates, check_candidates


def _tab(count: int) -> DeleteCandidates:
    ids = tuple(f"id-{n}" for n in range(count))
    return DeleteCandidates("observation_food", True, ids, ids)


def test_탭의_후보는_상한까지만_받는다() -> None:
    check_candidates([_tab(DELETE_TAB_MAX_IDS)])
    with pytest.raises(ValueError):
        check_candidates([_tab(DELETE_TAB_MAX_IDS + 1)])
