"""날짜 표현을 확정 날짜로 바꾸는 규칙 (resolve_date).

숫자로 센 "N일 전" 을 보호자가 말한 날(오늘) 기준으로 읽는지 본다. "언제부터였어요?" 에
"3일 전부터" 로 답하면 DATE_UNPARSEABLE 로 다시 묻던 것을 고친 것이다.
"N일 뒤·후" 는 다른 사건 기준으로 쓰는 경우가 많아("걸리고 3일 뒤에 나았어") 되묻는다.

기준점 (#497) — "이틀 전에 밥 먹었어" 는 오늘 기준, "소풍 이틀 전에 열났어" 는 소풍 기준이다.
관찰 · 일정의 생성 · 수정 · 조회가 모두 같은 규칙을 쓴다(tools/dates.py). 기준 일정은 모델이
이름(anchor_event)만 넘기고 날짜는 등록된 일정에서 코드가 찾는다. 보호자에게 물어 들은 날짜는
anchor_on 이다. "지난주" 처럼 하루로 정해지지 않는 말은 저장에서는 되묻고 조회에서는 기간이다.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import (
    DateNeedsAsking,
    DateParseError,
    DateRange,
    TemporalDirection,
    resolve_date,
    resolve_date_range,
    resolve_query_bounds,
)
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


@pytest.mark.parametrize("value", ["3일 뒤", "3일 후", "3일 뒤까지", "사흘 뒤"])
def test_기준이_없으면_며칠_뒤도_방향과_상관없이_오늘에서_센다(value: str) -> None:
    # 지난 일의 "3일 뒤" 를 막는 건 계산이 아니라 관찰 저장의 검사다 (#643 리뷰)
    for direction in ("past", "nearest", "future"):
        assert resolve_date(value, today=TODAY, direction=direction) == date(2026, 10, 2)


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


async def _seed_event(
    context: AgentContext, title: str, day: date, last: date | None = None
) -> None:
    await context.store.create_event(
        child_id=context.child_id,
        title=title,
        starts_at=datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST),
        ends_at=datetime(last.year, last.month, last.day, 17, 0, tzinfo=KST) if last else None,
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
    anchor = DateRange(start=date(2026, 10, 3), end=date(2026, 10, 4))
    assert resolve_date(value, today=TODAY, anchor=anchor) == want


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("전날", date(2026, 9, 30)),
        ("이틀 전", date(2026, 9, 29)),
        ("다음날", date(2026, 10, 4)),  # 시작일(10/1) 다음날이 아니다 (#643 리뷰)
        ("사흘 뒤", date(2026, 10, 6)),
    ],
)
def test_여러_날_일정은_앞쪽은_첫날에서_뒤쪽은_마지막_날에서_센다(value: str, want: date) -> None:
    camp = DateRange(start=date(2026, 10, 1), end=date(2026, 10, 4))  # 10/1~10/3 수련회
    assert resolve_date(value, today=TODAY, anchor=camp) == want


def test_여러_날_일정의_당일은_어느_날인지_되묻는다() -> None:
    camp = DateRange(start=date(2026, 10, 1), end=date(2026, 10, 4))
    with pytest.raises(DateNeedsAsking):
        resolve_date("당일", today=TODAY, anchor=camp)


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


@pytest.mark.parametrize(
    ("value", "direction", "start", "end"),
    [
        ("지난주", "past", date(2026, 9, 21), date(2026, 9, 28)),
        ("이번 주", "nearest", date(2026, 9, 28), date(2026, 10, 5)),
        ("지난달", "past", date(2026, 8, 1), date(2026, 9, 1)),
        # 화요일에 지난 일로 말한 "주말" 은 아직 안 온 이번 주말이 아니다 (#643 리뷰)
        ("주말", "past", date(2026, 9, 26), date(2026, 9, 28)),
        ("주말", "future", date(2026, 10, 3), date(2026, 10, 5)),
    ],
)
def test_조회에서는_주_주말_달을_기간으로_쓴다(
    value: str, direction: TemporalDirection, start: date, end: date
) -> None:
    # "지난주에 먹은 것 보여줘" 를 무슨 요일이었는지 되묻지 않는다 (#643 리뷰)
    span = resolve_date_range(value, today=TODAY, direction=direction)
    assert span == DateRange(start=start, end=end)


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


# ── 관찰 · 일정 tool 이 같은 규칙을 쓰는지 (#643 리뷰) ──────────────
async def test_여러_날_일정_다음날의_관찰은_마지막_날_다음날로_저장한다() -> None:
    context = _context()
    await _seed_event(context, "수련회", date(2026, 9, 20), last=date(2026, 9, 22))

    payload = await _call(
        context,
        "create_observation_health",
        {
            "raw_text": "수련회 다음날 열났어",
            "observed_on": "다음날",
            "anchor_event": "수련회",
            "symptom": ["발열"],
        },
    )

    assert payload["data"]["observed_on"] == "2026-09-23", payload


async def test_관찰은_오늘보다_뒤면_저장하지_않고_기준을_묻는다() -> None:
    # "걸리고 3일 뒤에 나았어" 를 오늘에서 세면 미래 날짜다. 다른 사건이 기준일 가능성이 크다
    context = _context()
    payload = await _call(
        context,
        "create_observation_health",
        {
            "raw_text": "걸리고 3일 뒤에 나았어",
            "observed_on": "3일 뒤",
            "temporal_direction": "past",
            "symptom": ["회복"],
        },
    )

    assert payload["success"] is False
    assert "anchor_event" in payload["error"]["message"]
    assert await context.store.query_observations(domain="health", child_id=context.child_id) == []


async def test_등록_안_된_기준_일정은_물어서_들은_날짜로_센다() -> None:
    # 소풍이 없으면 되묻고, 보호자가 "지난 금요일" 이라고 답하면 anchor_on 으로 받는다
    context = _context()
    args = {
        "raw_text": "소풍 전날 김밥 먹었어",
        "observed_on": "전날",
        "anchor_event": "소풍",
        "subject": "김밥",
    }
    asked = await _call(context, "create_observation_food", args)
    answered = await _call(
        context, "create_observation_food", {**args, "anchor_on": "지난주 금요일"}
    )

    assert asked["success"] is False and "anchor_on" in asked["error"]["message"]
    assert answered["data"]["observed_on"] == "2026-09-24", answered  # 금요일 9/25 의 전날


async def test_관찰_수정도_기준_일정을_받는다() -> None:
    # 되묻기 안내대로 update 에 anchor_event 를 넣어도 스키마에서 막히지 않는다 (#643 리뷰)
    context = _context()
    await _seed_event(context, "소풍", date(2026, 9, 25))
    created = await _call(
        context,
        "create_observation_food",
        {"raw_text": "김밥 먹었어", "observed_on": "오늘", "subject": "김밥"},
    )

    payload = await _call(
        context,
        "update_observation_food",
        {
            "observation_id": created["data"]["id"],
            "observed_on": "전날",
            "anchor_event": "소풍",
        },
    )

    assert payload["data"]["observed_on"] == "2026-09-24", payload


async def test_관찰_조회의_지난주는_기간으로_찾는다() -> None:
    context = _context()
    for raw, day in (
        ("월요일 카레", "2026-09-21"),
        ("일요일 우동", "2026-09-27"),
        ("오늘 죽", "오늘"),
    ):
        await _call(
            context,
            "create_observation_food",
            {"raw_text": raw, "observed_on": day, "subject": raw.split()[1]},
        )

    payload = await _call(
        context,
        "query_observation_food",
        {"date_from": "지난주", "date_to": "지난주", "temporal_direction": "past"},
    )

    assert payload["data"]["count"] == 2, payload


@pytest.mark.parametrize(
    ("bounds", "want"),
    [
        # 한쪽에만 기간이 와도 그 기간만 — 이번 주(죽)도 지지난주(떡)도 섞이지 않는다 (#643 리뷰)
        ({"date_from": "지난주"}, 2),
        ({"date_to": "지난주"}, 2),
        ({"date_from": "지난주에"}, 2),
        # 열린 쪽을 말했으면 그대로 연다
        ({"date_from": "지난주부터"}, 3),
        ({"date_to": "지난주까지"}, 3),
    ],
)
async def test_조회_경계가_한쪽만_와도_기간은_그_기간만_찾는다(
    bounds: dict[str, str], want: int
) -> None:
    context = _context()
    for raw, day in (
        ("지지난주 떡", "2026-09-20"),
        ("월요일 카레", "2026-09-21"),
        ("일요일 우동", "2026-09-27"),
        ("오늘 죽", "오늘"),
    ):
        await _call(
            context,
            "create_observation_food",
            {"raw_text": raw, "observed_on": day, "subject": raw.split()[1]},
        )

    payload = await _call(
        context, "query_observation_food", {**bounds, "temporal_direction": "past"}
    )

    assert payload["data"]["count"] == want, payload


def test_조회_경계_한쪽의_하루_표현은_열린_채로_둔다() -> None:
    # "오늘부터 앞으로 일정" 을 date_from="오늘" 하나로 받는 조회가 있다
    assert resolve_query_bounds("오늘", None, today=TODAY, direction="future") == (TODAY, None)


async def test_일정의_며칠_뒤는_기준이_있으면_거기서_없으면_오늘에서_센다() -> None:
    context = _context()
    await _seed_event(context, "소풍", date(2026, 10, 3))
    when = {"title": "정리하기", "starts_on": "사흘 뒤", "starts_time": "오후 2시"}

    from_picnic = await _call(context, "create_event", {**when, "anchor_event": "소풍"})
    from_today = await _call(context, "create_event", {**when, "temporal_direction": "future"})

    assert from_picnic["data"]["starts_at"].startswith("2026-10-06"), from_picnic
    assert from_today["data"]["starts_at"].startswith("2026-10-02"), from_today


async def test_새_일정이_오늘보다_앞이면_만들지_않고_기준을_묻는다() -> None:
    # "소풍 이틀 전에 장보기" 를 기준 없이 오늘에서 세면 지난 날짜 초안이 된다 (#643 리뷰)
    context = _context()
    payload = await _call(
        context,
        "create_event",
        {"title": "장보기", "starts_on": "이틀 전", "starts_time": "오후 3시"},
    )

    assert payload["success"] is False
    assert "anchor_event" in payload["error"]["message"]
    assert context.drafts.all() == ()


@pytest.mark.parametrize("tool", ["query_event", "query_observation_food"])
async def test_조회의_며칠_뒤는_막히지_않는다(tool: str) -> None:
    # 관찰 조회의 기본 방향은 past 다. 조회에는 날짜 제한이 없다 (#643 리뷰)
    payload = await _call(_context(), tool, {"date_from": "사흘 뒤"})

    assert payload["success"] is True, payload


async def _event_days(context: AgentContext) -> tuple[str, str]:
    draft = context.drafts.all()[-1]
    assert draft.ends_at is not None
    return draft.starts_at.date().isoformat(), draft.ends_at.date().isoformat()


async def test_일정_종료일도_기준_일정에서_센다() -> None:
    context = _context()
    await _seed_event(context, "소풍", date(2026, 10, 10))
    payload = await _call(
        context,
        "create_event",
        {
            "title": "캠프",
            "starts_on": "이틀 뒤",
            "ends_on": "사흘 뒤",
            "anchor_event": "소풍",
            "starts_time": "오전 9시",
            "ends_time": "오후 5시",
        },
    )

    assert payload["success"] is True, payload
    assert await _event_days(context) == ("2026-10-12", "2026-10-13")  # 10/15 가 아니다


async def test_일정_종료일만_기준과_함께_고칠_수_있다() -> None:
    context = _context()
    await _seed_event(context, "소풍", date(2026, 10, 10))
    await _seed_event(context, "캠프", date(2026, 10, 12))
    camp = (await _call(context, "query_event", {"title_query": "캠프"}))["data"]["events"][0]

    payload = await _call(
        context,
        "update_event",
        {
            "event_id": camp["id"],
            "ends_on": "사흘 뒤",
            "ends_time": "오후 5시",
            "anchor_event": "소풍",
        },
    )

    assert payload["success"] is True, payload
    assert await _event_days(context) == ("2026-10-12", "2026-10-13")


@pytest.mark.parametrize(
    ("starts_on", "ends_on", "want"),
    [
        # 기준이 없으면 종료일도 오늘에서 센다 — 시작일(10/1)에서 세어 10/4 가 되지 않는다
        ("이틀 뒤", "사흘 뒤", ("2026-10-01", "2026-10-02")),
        # 오늘에서 센 금요일(10/2)이 시작보다 앞이면 시작일에서 다시 센다
        ("다음 주 수요일", "금요일", ("2026-10-07", "2026-10-09")),
    ],
)
async def test_기준_없는_일정_종료일은_오늘에서_세고_시작보다_앞이면_시작일에서_센다(
    starts_on: str, ends_on: str, want: tuple[str, str]
) -> None:
    context = _context()
    payload = await _call(
        context,
        "create_event",
        {
            "title": "캠프",
            "starts_on": starts_on,
            "ends_on": ends_on,
            "starts_time": "오전 9시",
            "ends_time": "오후 5시",
            "temporal_direction": "future",
        },
    )

    assert payload["success"] is True, payload
    assert await _event_days(context) == want


class TestWeekdayParen:
    """공지 원문은 "11월 27일(금)" 처럼 요일을 괄호로 붙인다 — 붙여넣은 공지가 못 읽혀 일정 초안이
    안 나왔다 (#797, 2026-11-08 eval). 요일은 날짜를 검산하는 데만 쓴다."""

    TODAY = date(2026, 11, 8)  # 일요일

    @pytest.mark.parametrize(
        "text", ["11월 27일(금)", "11월 27일 (금)", "11월 27일(금요일)", "2026년 11월 27일(금)"]
    )
    def test_요일_괄호를_떼고_읽는다(self, text):
        assert resolve_date(text, today=self.TODAY, direction="future") == date(2026, 11, 27)

    def test_요일이_날짜와_안_맞으면_거절한다(self):
        """조용히 한쪽을 고르지 않는다 — 날짜를 잘못 옮겨 적었을 수 있다."""
        with pytest.raises(DateParseError, match="요일"):
            resolve_date("11월 27일(월)", today=self.TODAY, direction="future")

    def test_기간_조회도_같다(self):
        found = resolve_date_range("11월 27일(금)", today=self.TODAY, direction="future")
        assert (found.start, found.end) == (date(2026, 11, 27), date(2026, 11, 28))


async def test_붙여넣은_공지의_요일_괄호로_일정_초안을_만든다() -> None:
    """채팅에 붙여넣은 공지 원문 그대로 — Memory 가 날짜를 옮겨 적어도 초안이 나온다 (#797)."""
    context = _context()  # 2026-09-29(화)
    payload = await _call(
        context,
        "create_event",
        {"title": "가을 소풍", "starts_on": "10월 2일(금)", "starts_time": "오전 9시 30분"},
    )

    assert payload["success"] is True, payload
    assert payload["data"]["starts_at"].startswith("2026-10-02"), payload
