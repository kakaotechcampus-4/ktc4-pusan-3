"""행사 일정 — 일정 원문에서 열리는 날 · 그날 장소를 읽는다. 모델을 부르지 않는다(가짜 모델).

고정하는 것
- 일정 낱말이 없으면 모델을 부르지 않고 기간 안 매일이다 · 기간 밖 날은 열리지 않는다
- 모델은 원문을 구조로 옮기기만 한다. 요일 · 날짜 계산과 검증은 코드다
- 🚨 모르면 열리지 않는 날이다 — 근거가 원문에 없거나 · 값이 이상하거나 · 모델 · 원문 조회가
  실패하면
- 운영 시간 칸을 코드로 읽은 요일과 날마다 맞댄다. 갈리는 날은 열리지 않는다
- 같은 행사 · 같은 원문은 그날 한 번만 읽는다. 실패는 캐시하지 않는다

행사 이름 · 장소 · 원문은 지어낸 것이다.
"""

import asyncio
import json
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.agents.activity import event_schedule
from app.agents.activity.event_schedule import ReadEventSchedules, ScheduleCache
from app.agents.activity.events import Event
from app.agents.activity.schemas.common import EventKind
from app.agents.activity.store.inmemory import InMemoryEvents
from app.agents.activity.store.ports import EVENT_SCHEDULE_DAYS, EventSchedule
from app.agents.common.llm_client import LLMResponse, LLMUnavailableError
from app.core.event_raw import RawEventIntro

# 2026-11-02 월요일. 읽는 창은 11-02(월) ~ 11-08(일)
TODAY = date(2026, 11, 2)
WINDOW = [TODAY + timedelta(days=i) for i in range(EVENT_SCHEDULE_DAYS)]
MON, TUE, WED, THU, FRI, SAT, SUN = WINDOW
EVENT = Event(
    content_id="1",
    name="달빛 체조 교실",
    kind=EventKind.EVENT,
    starts_on=date(2026, 10, 1),
    ends_on=date(2026, 11, 30),
    lat=35.1,
    lon=129.0,
)


def intro(*, place="바람숲공원 잔디광장", playtime="19:00~20:30", program="") -> RawEventIntro:
    return RawEventIntro(age_limit="", place=place, playtime=playtime, program=program)


def reply(
    schedule="restricted",
    *,
    open_weekdays=(),
    closed_weekdays=(),
    monthly=(),
    dates=(),
    venue_dates=(),
    evidence=(),
) -> dict:
    return {
        "schedule": schedule,
        "open_weekdays": list(open_weekdays),
        "closed_weekdays": list(closed_weekdays),
        "monthly": [{"nth": n, "weekday": w} for n, w in monthly],
        "dates": list(dates),
        "venue_dates": [{"venue": v, "dates": list(ds)} for v, ds in venue_dates],
        "evidence": list(evidence),
    }


class FakeClient:
    """정해 둔 답을 돌려준다. 예외를 넣으면 그 예외를 낸다. 받은 메시지를 남긴다."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[dict], dict | None]] = []

    async def chat(self, messages, *, response_format=None, **_) -> LLMResponse:
        self.calls.append((messages, response_format))
        answer = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        return LLMResponse(message=SimpleNamespace(content=content), usage={}, latency_ms=1)


def reader(client=None, *, info=None, overview="", cache=None, **kwargs) -> ReadEventSchedules:
    source = InMemoryEvents(
        intros={EVENT.content_id: info or intro()},
        overviews={EVENT.content_id: overview},
        **kwargs,
    )
    return ReadEventSchedules(source, client=client or FakeClient(), cache=cache or ScheduleCache())


async def read(client=None, *, event=EVENT, today=TODAY, **kwargs) -> EventSchedule:
    return await reader(client, **kwargs).schedule_of(event, today=today)


class TestNoScheduleWords:
    async def test_일정_낱말이_없으면_모델을_부르지_않고_기간_안_매일이다(self):
        client = FakeClient(reply())

        schedule = await read(client, overview="가족이 함께 몸을 움직이는 저녁 체조 행사다.")

        assert client.calls == []
        assert schedule.open_days == frozenset(WINDOW)
        assert schedule.day_places == ()

    async def test_기간_밖_날은_열리지_않는다(self):
        short = replace(EVENT, starts_on=WED, ends_on=FRI)
        schedule = await read(event=short)
        assert schedule.open_days == {WED, THU, FRI}

    @pytest.mark.parametrize("place", ["수도권 전역", "바람숲공원 등 시 전역"])
    async def test_장소가_전역이면_모델을_부르지_않고_모른다(self, place):
        client = FakeClient(reply("every_day"))
        schedule = await read(client, info=intro(place=place))
        assert client.calls == []
        assert schedule.open_days == frozenset()

    async def test_시각_없이_상이하다고만_적은_운영_시간은_모른다(self):
        client = FakeClient(reply("every_day"))
        schedule = await read(client, info=intro(playtime="회차별 상이"))
        assert client.calls == []
        assert schedule.open_days == frozenset()


class TestModel:
    async def test_원문을_모델에_주고_구조로_받는다(self):
        client = FakeClient(reply("every_day"))

        await read(client, overview="매주 저녁 체조를 한다. 10월 1일 시작.")

        ((messages, response_format),) = client.calls
        text = messages[-1]["content"]
        for part in ("달빛 체조 교실", "2026-10-01 ~ 2026-11-30", "바람숲공원 잔디광장"):
            assert part in text
        assert "매주 저녁 체조를 한다" in text
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True

    async def test_그_요일만_열린다(self):
        client = FakeClient(
            reply(open_weekdays=[5], evidence=["매주 토요일 저녁"]),
        )
        schedule = await read(client, overview="매주 토요일 저녁 두 번 열린다.")
        assert schedule.open_days == {SAT}

    async def test_쉬는_요일을_뺀다(self):
        client = FakeClient(reply("every_day", closed_weekdays=[0], evidence=["월요일 휴무"]))
        schedule = await read(client, info=intro(playtime="10:00~18:00 (월요일 휴무)"))
        assert schedule.open_days == frozenset(WINDOW) - {MON}

    async def test_쉬는_요일만_적은_제한은_매일에서_그_요일을_뺀다(self):
        client = FakeClient(reply(closed_weekdays=[0], evidence=["월요일 휴무"]))
        schedule = await read(client, info=intro(playtime="10:00~18:00 (월요일 휴무)"))
        assert schedule.open_days == frozenset(WINDOW) - {MON}

    async def test_매월_몇째_주_요일만_열린다(self):
        """11-07 은 첫째 토요일이다. 마지막 주(-1)는 11-28 이라 창 밖이다."""
        client = FakeClient(
            reply(monthly=[(1, 5), (-1, 6)], evidence=["매월 첫째 주 토요일과 마지막 주 일요일"])
        )
        schedule = await read(client, overview="매월 첫째 주 토요일과 마지막 주 일요일에 연다.")
        assert schedule.open_days == {SAT}

    async def test_매월_몇째_주와_같은_요일의_매주는_버린다(self):
        """모델이 "매월 첫째 주 토요일 · 매주 일요일" 의 토요일을 매주로도 적었다. 11-09(월)부터
        보면 11-14 는 둘째 토요일이라 닫혀 있고 11-15(일)만 열린다."""
        client = FakeClient(
            reply(
                open_weekdays=[5, 6],
                monthly=[(1, 5)],
                evidence=["매월 첫째 주 토요일과 매주 일요일"],
            )
        )
        schedule = await read(
            client, today=date(2026, 11, 9), overview="매월 첫째 주 토요일과 매주 일요일에 연다."
        )
        assert schedule.open_days == {date(2026, 11, 15)}

    async def test_그_날짜만_열리고_기간_밖_날짜는_버린다(self):
        client = FakeClient(
            reply(dates=["2026-11-04", "2026-11-07", "2026-12-25"], evidence=["11월 4일, 7일"])
        )
        schedule = await read(client, overview="11월 4일, 7일에 연다.")
        assert schedule.open_days == {WED, SAT}

    async def test_날짜와_요일을_같이_적었으면_둘_다_맞는_날만_연다(self):
        """모델이 날짜 목록과 함께 "토 · 일" 요일도 적었다 — 날짜 밖 일요일(11-08)을 열면 안
        된다 (11-03 실측)."""
        client = FakeClient(
            reply(
                open_weekdays=[4, 5, 6],
                dates=["2026-11-06", "2026-11-07"],
                evidence=["11월 6일(금), 7일(토) 저녁"],
            )
        )
        schedule = await read(client, overview="11월 6일(금), 7일(토) 저녁에 연다.")
        assert schedule.open_days == {FRI, SAT}

    async def test_기간을_날짜로_다_적고_요일을_같이_적었으면_그_요일만_연다(self):
        """ "11월 2일부터 8일까지 주말" 의 기간을 날짜로 다 옮겼다 — 날짜만 보면 평일도 연다."""
        week = [day.isoformat() for day in WINDOW]
        client = FakeClient(
            reply(open_weekdays=[5, 6], dates=week, evidence=["11월 2일부터 8일까지 주말"])
        )
        schedule = await read(client, overview="11월 2일부터 8일까지 주말에 연다.")
        assert schedule.open_days == {SAT, SUN}

    async def test_장소마다_날짜가_다르면_그날_장소를_싣는다(self):
        program = "1. 바람숲공원 - 10.31.(토)\n2. 모래섬 해변 - 11.7.(토)"
        client = FakeClient(
            reply(
                venue_dates=[("바람숲공원", ["2026-10-31"]), ("모래섬 해변", ["2026-11-07"])],
                evidence=["모래섬 해변 - 11.7.(토)"],
            )
        )

        schedule = await read(client, info=intro(place="바람숲공원 / 모래섬 해변", program=program))

        assert schedule.open_days == {SAT}
        assert schedule.day_places == (
            (date(2026, 10, 31), "바람숲공원"),
            (SAT, "모래섬 해변"),
        )

    async def test_운영_시간_칸의_요일과_갈리는_날은_열리지_않는다(self):
        """운영 시간은 토요일만인데 모델이 매일이라고 했다 — 토요일만 둘이 맞는다."""
        client = FakeClient(reply("every_day"))
        schedule = await read(client, info=intro(playtime="(토) 19:00~20:30"))
        assert schedule.open_days == {SAT}

    async def test_운영_시간의_범위_요일과도_맞댄다(self):
        client = FakeClient(reply("every_day"))
        schedule = await read(client, info=intro(playtime="화요일~토요일 10:00~18:00"))
        assert schedule.open_days == {TUE, WED, THU, FRI, SAT}

    async def test_근거_구절의_칸_이름은_떼고_원문과_대조한다(self):
        client = FakeClient(reply(open_weekdays=[5], evidence=["개요: 매주 토요일 저녁"]))
        schedule = await read(client, overview="매주 토요일 저녁 두 번 열린다.")
        assert schedule.open_days == {SAT}


class TestUnknown:
    """🚨 모르면 열리지 않는 날이다 — 기간으로 메우지 않는다."""

    OVERVIEW = "매주 토요일 저녁 두 번 열린다."

    @pytest.mark.parametrize(
        "answer",
        [
            reply("unknown"),
            reply(open_weekdays=[5]),  # 제한하는데 근거가 없다
            reply(open_weekdays=[5], evidence=["매주 일요일 아침"]),  # 원문에 없는 구절
            reply(open_weekdays=[7], evidence=["매주 토요일 저녁"]),  # 없는 요일
            reply(monthly=[(6, 5)], evidence=["매주 토요일 저녁"]),  # 여섯째 주는 없다
            reply(dates=["2026-12-25"], evidence=["매주 토요일 저녁"]),  # 열리는 날이 기간 밖뿐
            reply(  # 원문에 없는 장소
                venue_dates=[("구름언덕 광장", ["2026-11-07"])], evidence=["매주 토요일 저녁"]
            ),
            # 요일 숫자를 하나 밀려 적었다 — 근거는 토요일인데 일요일(6)
            reply(open_weekdays=[6], evidence=["매주 토요일 저녁"]),
            "{이상한 답",
            {"schedule": "restricted"},  # 칸이 빠졌다
        ],
    )
    async def test_검증을_통과하지_못하면_모른다(self, answer):
        schedule = await read(FakeClient(answer), overview=self.OVERVIEW)
        assert schedule.open_days == frozenset()

    async def test_모델_호출이_실패하면_모른다(self):
        client = FakeClient(LLMUnavailableError("연결 실패"))
        schedule = await read(client, overview=self.OVERVIEW)
        assert schedule.open_days == frozenset()

    async def test_모델이_늦으면_기다리지_않고_모른다(self, monkeypatch):
        monkeypatch.setattr(event_schedule, "READ_TIMEOUT_S", 0.01)

        class Slow(FakeClient):
            async def chat(self, messages, **kwargs):
                await asyncio.sleep(1)
                return await super().chat(messages, **kwargs)

        schedule = await read(Slow(reply("every_day")), overview=self.OVERVIEW)
        assert schedule.open_days == frozenset()

    @pytest.mark.parametrize("failing", ["fail_intro", "fail_overview"])
    async def test_원문을_못_읽으면_모른다(self, failing):
        client = FakeClient(reply("every_day"))
        schedule = await read(client, **{failing: [EVENT.content_id]})
        assert client.calls == []
        assert schedule.open_days == frozenset()


class TestWeekdayQuote:
    """모델이 요일 숫자를 하나 밀려 적는다 — 이름 붙인 요일은 근거 구절에 요일로 있어야 한다."""

    async def test_쉬는_요일이_근거에_요일로_없으면_모른다(self):
        """ "월요일은 쉰다" 를 화요일(1)로 적었다 (11-03 실측). "매일" 은 요일을 적은 것이
        아니다."""
        client = FakeClient(
            reply(
                "every_day", closed_weekdays=[1], evidence=["월요일은 쉬고 나머지 날은 매일 연다"]
            )
        )
        schedule = await read(client, overview="월요일은 쉬고 나머지 날은 매일 연다.")
        assert schedule.open_days == frozenset()

    async def test_여는_요일_범위_밖을_쉬는_요일로_적어도_된다(self):
        """ "화요일부터 일요일까지" 만 인용하고 월요일(0)을 쉰다고 적었다 — 범위 밖이 쉬는
        날이다."""
        client = FakeClient(
            reply(
                open_weekdays=[1, 2, 3, 4, 5, 6],
                closed_weekdays=[0],
                evidence=["화요일부터 일요일까지 운영"],
            )
        )
        schedule = await read(client, overview="화요일부터 일요일까지 운영한다.")
        assert schedule.open_days == frozenset(WINDOW) - {MON}

    @pytest.mark.parametrize(
        ("quote", "weekdays"),
        [
            ("화요일~토요일 운영", [1, 2, 3, 4, 5]),
            ("매주 금, 토, 일 저녁", [4, 5, 6]),
            ("주말에만 연다", [5, 6]),
            ("(토) 19:00 시작", [5]),
        ],
    )
    async def test_근거가_요일을_적는_여러_모양을_읽는다(self, quote, weekdays):
        client = FakeClient(reply(open_weekdays=weekdays, evidence=[quote]))
        schedule = await read(client, overview=f"{quote}.")
        assert schedule.open_days == {day for day in WINDOW if day.weekday() in weekdays}


class TestCache:
    OVERVIEW = "매주 토요일 저녁 두 번 열린다."

    async def test_같은_행사는_그날_한_번만_읽는다(self):
        client = FakeClient(reply(open_weekdays=[5], evidence=["매주 토요일 저녁"]))
        schedules = reader(client, overview=self.OVERVIEW)

        first = await schedules.schedule_of(EVENT, today=TODAY)
        second = await schedules.schedule_of(EVENT, today=TODAY)

        assert len(client.calls) == 1
        assert first == second == EventSchedule(open_days=frozenset({SAT}))

    async def test_날이_바뀌면_다시_읽는다(self):
        client = FakeClient(reply(open_weekdays=[5], evidence=["매주 토요일 저녁"]))
        schedules = reader(client, overview=self.OVERVIEW)

        await schedules.schedule_of(EVENT, today=TODAY)
        tomorrow = await schedules.schedule_of(EVENT, today=TUE)

        assert len(client.calls) == 2
        assert SAT in tomorrow.open_days

    async def test_원문이_바뀌면_다시_읽는다(self):
        client = FakeClient(reply(open_weekdays=[5], evidence=["매주 토요일 저녁"]))
        cache = ScheduleCache()

        await reader(client, overview=self.OVERVIEW, cache=cache).schedule_of(EVENT, today=TODAY)
        await reader(client, overview=self.OVERVIEW + " 우천 취소.", cache=cache).schedule_of(
            EVENT, today=TODAY
        )

        assert len(client.calls) == 2

    async def test_실패는_캐시하지_않는다(self):
        client = FakeClient(
            LLMUnavailableError("연결 실패"),
            reply(open_weekdays=[5], evidence=["매주 토요일 저녁"]),
        )
        schedules = reader(client, overview=self.OVERVIEW)

        assert (await schedules.schedule_of(EVENT, today=TODAY)).open_days == frozenset()
        assert (await schedules.schedule_of(EVENT, today=TODAY)).open_days == {SAT}
