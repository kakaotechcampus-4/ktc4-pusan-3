"""날짜 표현을 확정 날짜로 바꾸는 규칙 (resolve_date).

숫자로 센 "N일 전" 을 보호자가 말한 날(오늘) 기준으로 읽는지 본다. "언제부터였어요?" 에
"3일 전부터" 로 답하면 DATE_UNPARSEABLE 로 다시 묻던 것을 고친 것이다.
"N일 뒤·후" 는 다른 사건 기준으로 쓰는 경우가 많아("걸리고 3일 뒤에 나았어") 되묻는다.

기준점 (#497) — "이틀 전에 밥 먹었어" 는 오늘 기준, "소풍 이틀 전에 열났어" 는 소풍 기준이다.
기준 일정은 모델이 이름(anchor_event)만 넘기고 날짜는 등록된 일정에서 코드가 찾는다.
"지난주" 처럼 하루로 정해지지 않는 말은 날짜를 고르지 않고 되묻는다.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import DateNeedsAsking, DateParseError, resolve_date
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


def _context() -> AgentContext:
    now = datetime(2026, 9, 29, 9, 0, tzinfo=KST)
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=now,
        timezone=KST,
        store=InMemoryStore(now=now),
    )


async def _seed_event(context: AgentContext, title: str, day: date) -> None:
    await context.store.create_event(
        child_id=context.child_id,
        title=title,
        starts_at=datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={"event_type": "episodic", "category": "institution"},
    )


async def _call(context: AgentContext, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return (await execute_tool(tool, args, context)).to_payload()


async def test_관찰_저장에_3일_전부터를_넣으면_그날로_저장한다() -> None:
    payload = await _call(
        _context(),
        "create_observation_health",
        {"raw_text": "요즘 기침해", "observed_on": "3일 전부터", "symptom": ["기침"]},
    )

    assert payload["success"] is True, payload
    assert payload["data"]["observed_on"] == "2026-09-26"


# ── 한글로 센 날수 · 기준점 (#497) ──────────────────────────────
@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("이틀 전", date(2026, 9, 27)),
        ("이틀 전에", date(2026, 9, 27)),
        ("하루 전", date(2026, 9, 28)),
        ("사흘 전", date(2026, 9, 26)),
        ("열흘 전", date(2026, 9, 19)),
        ("보름 전", date(2026, 9, 14)),
        ("일주일 전", date(2026, 9, 22)),
        ("한 주 전", date(2026, 9, 22)),
        ("2주 전", date(2026, 9, 15)),
        ("그끄저께", date(2026, 9, 26)),
        ("어젯밤", date(2026, 9, 28)),
    ],
)
def test_기준_일정_없이_말한_날수는_오늘에서_센다(value: str, want: date) -> None:
    # "그저께" · "2일 전" 은 받는데 같은 뜻의 "이틀 전" 만 되묻던 것 (#497)
    assert resolve_date(value, today=TODAY, direction="past") == want


def test_오늘_기준_며칠_뒤는_앞으로의_일정에서만_받는다() -> None:
    assert resolve_date("사흘 뒤", today=TODAY, direction="future") == date(2026, 10, 2)
    with pytest.raises(DateNeedsAsking):
        resolve_date("사흘 뒤", today=TODAY, direction="past")


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("이틀 전", date(2026, 10, 1)),
        ("전날", date(2026, 10, 2)),
        ("당일", date(2026, 10, 3)),
        ("다음 날", date(2026, 10, 4)),
        ("3일 뒤", date(2026, 10, 6)),
    ],
)
def test_기준_일정이_있으면_그_일정_날짜에서_센다(value: str, want: date) -> None:
    assert resolve_date(value, today=TODAY, anchor=date(2026, 10, 3)) == want


@pytest.mark.parametrize("value", ["전날", "다음날", "당일"])
def test_기준_일정_없는_전날_다음날은_되묻는다(value: str) -> None:
    with pytest.raises(DateNeedsAsking):
        resolve_date(value, today=TODAY)


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("지지난주 화요일", date(2026, 9, 15)),
        ("저저번주 화요일", date(2026, 9, 15)),
    ],
)
def test_두_주_전_요일을_날짜로_바꾼다(value: str, want: date) -> None:
    assert resolve_date(value, today=TODAY, direction="past") == want


@pytest.mark.parametrize("value", ["지난주", "지난 주말", "저저번주", "지난달", "며칠 전"])
def test_하루로_정해지지_않는_말은_날짜를_고르지_않고_되묻는다(value: str) -> None:
    with pytest.raises(DateNeedsAsking):
        resolve_date(value, today=TODAY, direction="past")


async def test_관찰의_기준_일정은_등록된_일정_날짜에서_센다() -> None:
    context = _context()
    await _seed_event(context, "소풍", date(2026, 9, 25))

    payload = await _call(
        context,
        "create_observation_health",
        {
            "raw_text": "소풍 이틀 전에 열났어",
            "observed_on": "이틀 전",
            "anchor_event": "소풍",
            "symptom": ["발열"],
        },
    )

    assert payload["success"] is True, payload
    assert payload["data"]["observed_on"] == "2026-09-23"


@pytest.mark.parametrize(
    ("anchor_event", "seeded"),
    [
        ("소풍", ()),  # 등록된 소풍이 없다
        ("소풍", (("소풍", date(2026, 5, 1)), ("소풍", date(2026, 9, 25)))),  # 소풍이 둘이다
        # 같은 날 두 건 — 날짜는 같아도 어느 일정인지 고르지 않는다
        ("소풍", (("소풍", date(2026, 9, 25)), ("소풍", date(2026, 9, 25)))),
        ("소풍", (("소풍", date(2026, 10, 5)),)),  # 소풍 이틀 전이 아직 안 왔다
        # 빈 이름 — 저장소가 "조건 없음" 으로 읽어 병원 일정이 기준이 되면 안 된다
        (" ", (("병원", date(2026, 9, 25)),)),
    ],
)
async def test_기준_일정이_한_건으로_정해지지_않으면_저장하지_않는다(
    anchor_event: str, seeded: tuple[tuple[str, date], ...]
) -> None:
    context = _context()
    for title, day in seeded:
        await _seed_event(context, title, day)

    payload = await _call(
        context,
        "create_observation_health",
        {
            "raw_text": "소풍 이틀 전에 열났어",
            "observed_on": "이틀 전",
            "anchor_event": anchor_event,
            "symptom": ["발열"],
        },
    )

    assert payload["success"] is False
    assert payload["error"]["code"] == "DATE_UNPARSEABLE"
    assert await context.store.query_observations(domain="health", child_id=context.child_id) == []
