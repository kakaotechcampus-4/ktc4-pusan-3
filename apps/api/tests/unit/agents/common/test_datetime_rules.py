"""날짜 표현을 확정 날짜로 바꾸는 규칙 (resolve_date).

숫자로 센 "N일 전" 을 보호자가 말한 날(오늘) 기준으로 읽는지 본다. "언제부터였어요?" 에
"3일 전부터" 로 답하면 DATE_UNPARSEABLE 로 다시 묻던 것을 고친 것이다.
"N일 뒤·후" 는 다른 사건 기준으로 쓰는 경우가 많아("걸리고 3일 뒤에 나았어") 되묻는다.
"""

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import DateParseError, resolve_date
from app.agents.memory.context import AgentContext
from app.agents.memory.registry import execute_tool
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
TODAY = date(2026, 9, 29)


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("3일 전", date(2026, 9, 26)),
        ("3일전", date(2026, 9, 26)),
        ("3일 전부터", date(2026, 9, 26)),
        ("30일 전", date(2026, 8, 30)),  # 달을 넘긴다
        ("0일 전", date(2026, 9, 29)),
    ],
)
def test_오늘_기준_며칠_전을_날짜로_바꾼다(value: str, want: date) -> None:
    assert resolve_date(value, today=TODAY) == want


@pytest.mark.parametrize("value", ["2일 뒤", "5일 후", "3일 뒤까지"])
def test_며칠_뒤_후는_기준이_모호해_되묻는다(value: str) -> None:
    # 오늘 기준으로 읽으면 "걸리고 3일 뒤에 나았어" 가 미래 날짜로 저장된다
    with pytest.raises(DateParseError):
        resolve_date(value, today=TODAY)


def test_며칠_전은_direction_과_상관없이_과거다() -> None:
    # 표현 자체가 방향을 말한다. 모델이 future 로 넘겨도 뒤집지 않는다
    assert resolve_date("3일 전", today=TODAY, direction="future") == date(2026, 9, 26)


async def test_관찰_저장에_3일_전부터를_넣으면_그날로_저장한다() -> None:
    now = datetime(2026, 9, 29, 9, 0, tzinfo=KST)
    context = AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=now,
        timezone=KST,
        store=InMemoryStore(now=now),
    )

    result = await execute_tool(
        "create_observation_health",
        {"raw_text": "요즘 기침해", "observed_on": "3일 전부터", "symptom": ["기침"]},
        context,
    )

    payload = result.to_payload()
    assert payload["success"] is True, payload
    assert payload["data"]["observed_on"] == "2026-09-26"
