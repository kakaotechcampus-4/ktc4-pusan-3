"""오늘 한 번 "안 먹었다" 는 묻고, 식사 태도 · 경향만 생활 기록으로 남긴다 (#929, D18).

프롬프트 두 곳과 끼니 이름 거절 문구가 같은 두 갈래를 말해야 한다. 한 곳이 옛 규칙
("밥·끼니를 안 먹는다는 말은 식사 태도")을 말하면 모델이 그쪽으로 끌려간다.
모델 행동은 라이브 eval(T76~T81 · C12 · C13)이 본다 — 여기서는 문구만 지킨다.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.prompt import build_system_prompt
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=KST)


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


def test_식사_태도와_오늘_한_번_안_먹은_말을_가른다(context: AgentContext) -> None:
    prompt = build_system_prompt(context)

    # 옛 한 줄은 태도(묻지 않음)와 메뉴 모름(묻기) 두 규칙에 다 걸렸다
    assert "밥·끼니를 안 먹는다는 말은" not in prompt
    assert "투정 · 거부" in prompt
    assert "오늘 한 번 안 먹었다 · 조금 먹었다는 말은 무엇인지 알아야 저장한다" in prompt
    # 묻는 규칙은 [되물어야 할 때] 에 이미 있다. 태도 줄이 거기로 보낸다
    assert "없으면 [되물어야 할 때] 를 따른다" in prompt
    assert "먹었다는데 무엇인지 없으면" in prompt


async def test_끼니_이름_거절은_한_번_묻고_다시_묻지_않게_한다(context: AgentContext) -> None:
    result = await execute_tool(
        "create_observation_food",
        {"raw_text": "오늘 점심 조금밖에 안 먹었어", "observed_on": "오늘", "subject": "점심"},
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    # 옛 문구는 "안 먹는다 → routine" 한 갈래라 오늘 한 번 안 먹은 말도 routine 으로 보냈다
    assert "안 먹는다는 식사 태도면" not in result.error["message"]
    assert "한 번 묻고" in result.error["message"]
    assert "다시 묻지 않고 mealtime" in result.error["message"]
    assert await context.store.query_observations(domain="food", child_id=context.child_id) == []
