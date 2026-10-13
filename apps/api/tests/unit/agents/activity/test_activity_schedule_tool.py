"""일정 조회 tool — 날 종류(평일 · 주말 · 공휴일) · 바쁜 칸 · 비는 칸을 코드가 계산한다.

- 공휴일을 모르면 개천절 오전을 "어린이집 가 있는 시간"으로 읽는다 — 공휴일 상수로 가른다.
- 비는 시간은 DAY_START ~ DAY_END 안에서, 오늘이면 지금부터 센다. 일정 충돌은 모델이 안 본다.
- 일정 제목은 싣지 않는다.
"""

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.activity.context import ActivityContext
from app.agents.activity.schemas.common import DayLabel
from app.agents.activity.schemas.outing import LookupScheduleArgs
from app.agents.activity.store.inmemory import InMemorySchedule, in_memory_ports
from app.agents.activity.store.ports import ScheduleBlock
from app.agents.activity.tools.outing import lookup_schedule

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
WEDNESDAY = datetime(2026, 10, 7, 6, tzinfo=KST)  # 하루 범위(08시) 전


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=KST)


def block(starts, ends=None, *, all_day=False) -> ScheduleBlock:
    return ScheduleBlock(starts_at=starts, ends_at=ends, all_day=all_day)


async def schedule(blocks=(), *, now=WEDNESDAY, day=DayLabel.TODAY):
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=now,
        timezone=KST,
        ports=in_memory_ports(
            CHILD, date(2024, 1, 1), schedule=InMemorySchedule({CHILD: list(blocks)})
        ),
    )
    result = await lookup_schedule(ctx, LookupScheduleArgs(day=day))
    assert result.success is True
    return result.data["days"]


def spans(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"start": a, "end": b} for a, b in pairs]


class TestDayType:
    async def test_평일(self):
        (day,) = await schedule()
        assert (day["date"], day["weekday"], day["day_type"]) == ("2026-10-07", "수", "weekday")

    async def test_대체공휴일은_공휴일(self):
        """개천절(토)의 대체공휴일인 월요일. 모르면 어린이집 가는 날로 읽는다."""
        (day,) = await schedule(now=datetime(2026, 10, 5, 6, tzinfo=KST))
        assert day["day_type"] == "holiday"

    async def test_주말(self):
        days = await schedule(day=DayLabel.THIS_WEEKEND)
        assert [d["day_type"] for d in days] == ["weekend", "weekend"]

    async def test_공휴일을_넣어_두지_않은_해는_주말만_본다(self):
        (day,) = await schedule(now=datetime(2028, 1, 3, 6, tzinfo=KST))  # 월요일
        assert day["day_type"] == "weekday"


class TestFreeTime:
    async def test_일정이_없으면_하루_범위가_다_빈다(self):
        (day,) = await schedule()
        assert (day["busy"], day["free"]) == ([], spans(("08:00", "20:00")))

    async def test_일정을_빼고_비는_칸을_준다(self):
        (day,) = await schedule([block(at(7, 10), at(7, 12)), block(at(7, 15), at(7, 16))])
        assert day["busy"] == spans(("10:00", "12:00"), ("15:00", "16:00"))
        assert day["free"] == spans(("08:00", "10:00"), ("12:00", "15:00"), ("16:00", "20:00"))

    async def test_겹친_일정은_합친다(self):
        (day,) = await schedule([block(at(7, 10), at(7, 12)), block(at(7, 11), at(7, 13))])
        assert day["busy"] == spans(("10:00", "13:00"))

    async def test_오늘은_지금부터_센다(self):
        (day,) = await schedule(now=at(7, 14, 30))
        assert day["free"] == spans(("14:30", "20:00"))

    async def test_하루_범위_밖은_자른다(self):
        (day,) = await schedule([block(at(7, 6), at(7, 9)), block(at(7, 19), at(7, 22))])
        assert day["busy"] == spans(("08:00", "09:00"), ("19:00", "20:00"))

    async def test_끝_시각이_없는_일정은_한_시간으로_본다(self):
        (day,) = await schedule([block(at(7, 10))])
        assert day["busy"] == spans(("10:00", "11:00"))

    async def test_종일_일정이면_비는_칸이_없다(self):
        (day,) = await schedule([block(at(7, 0), all_day=True)])
        assert (day["all_day_event"], day["free"]) == (True, [])

    async def test_주말은_날마다_따로_센다(self):
        sat, sun = await schedule([block(at(10, 9), at(10, 18))], day=DayLabel.THIS_WEEKEND)
        assert sat["free"] == spans(("08:00", "09:00"), ("18:00", "20:00"))
        assert sun["free"] == spans(("08:00", "20:00"))

    async def test_일정_제목은_싣지_않는다(self):
        (day,) = await schedule([block(at(7, 10), at(7, 12))])
        assert set(day) == {"date", "weekday", "day_type", "all_day_event", "busy", "free"}
