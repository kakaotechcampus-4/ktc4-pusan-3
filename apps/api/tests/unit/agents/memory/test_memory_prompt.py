"""Memory system prompt 에서 보호자에게 새는 낱말."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.memory.context import AgentContext
from app.agents.memory.prompt import build_system_prompt
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=KST)


def test_프롬프트에_담당_Agent_가_없다() -> None:
    """모델이 이 낱말을 그대로 옮겨 적어 "담당 Agent가 답해 드릴게요" 를 보호자에게 보냈다."""
    context = AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )

    assert "담당 Agent" not in build_system_prompt(context)
