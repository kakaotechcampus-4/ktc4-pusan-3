"""05 가 닫힌 Agent 를 guard 로 알릴 때의 code.

화면은 `safety_unknown` 일 때만 알레르기 등록 안내를 그린다(suggestions/page.tsx). Growth 는 안전이
아닌 이유로도 닫혀서(`closed.*` · `doc.no_row`) 상태만 보면 그 안내가 잘못 뜬다.
"""

import pytest

from app.agents import entrypoint
from app.agents.common.readout import Readout
from app.api.v1.routers.suggestions import _guard_code


def outcome(agent: str, status: str, *readouts: Readout) -> entrypoint.SuggestOutcome:
    return entrypoint.SuggestOutcome(agent, None, status, (), tuple(readouts), None)


def readout(kind: str) -> Readout:
    return Readout(kind=kind, body="문구", authored_by="code")


@pytest.mark.parametrize("agent", ["food", "activity", "growth"])
def test_안전_때문에_닫히면_safety_unknown(agent):
    assert _guard_code(outcome(agent, "blocked", readout("blocked"))) == "safety_unknown"


@pytest.mark.parametrize("agent", ["food", "activity"])
def test_Food_Activity_는_읽을_수_있는_문구가_없어도_safety_unknown(agent):
    """지금 이 둘의 blocked 는 안전 조회 실패뿐이다 — 기존 동작을 그대로 둔다."""
    assert _guard_code(outcome(agent, "blocked")) == "safety_unknown"


@pytest.mark.parametrize("kind", ["closed", "notice"])
def test_Growth_가_안전이_아닌_이유로_닫히면_closed(kind):
    assert _guard_code(outcome("growth", "blocked", readout(kind))) == "closed"


def test_Growth_닫힘_문구가_없으면_closed(kind="closed"):
    assert _guard_code(outcome("growth", "blocked")) == "closed"


def test_지원하지_않는_Agent_는_unsupported():
    assert _guard_code(outcome("growth", "unsupported")) == "unsupported"


def test_정상_결과는_guard_가_없다():
    assert _guard_code(outcome("growth", "completed")) is None
