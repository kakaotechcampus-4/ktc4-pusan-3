"""일정 알림 시각 계산 규칙 (#594)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.rules.reminder import ReminderLead, fits, remind_at, starts_window

KST = timezone(timedelta(hours=9))


def test_timed_counts_back_from_start():
    start = datetime(2026, 10, 28, 11, 0, tzinfo=KST)
    assert remind_at(ReminderLead.HOURS_1, starts_at=start, all_day=False) == datetime(
        2026, 10, 28, 10, 0, tzinfo=KST
    )
    assert remind_at(ReminderLead.AT_START, starts_at=start, all_day=False) == start


def test_all_day_rings_at_9am_kst_even_when_start_is_read_in_utc():
    # 종일 일정은 KST 00:00 에 시작한다 — UTC 로는 전날 15:00 이다.
    start = datetime(2026, 10, 28, 0, 0, tzinfo=KST).astimezone(timezone.utc)
    assert remind_at(ReminderLead.DAY_OF_9AM, starts_at=start, all_day=True) == datetime(
        2026, 10, 28, 9, 0, tzinfo=KST
    )
    assert remind_at(ReminderLead.WEEKS_1_9AM, starts_at=start, all_day=True) == datetime(
        2026, 10, 21, 9, 0, tzinfo=KST
    )


@pytest.mark.parametrize(
    ("lead", "all_day"),
    [(ReminderLead.HOURS_1, True), (ReminderLead.DAY_OF_9AM, False)],
)
def test_lead_of_the_other_kind_is_refused(lead, all_day):
    start = datetime(2026, 10, 28, tzinfo=KST)
    assert not fits(lead, all_day=all_day)
    with pytest.raises(ValueError):
        remind_at(lead, starts_at=start, all_day=all_day)


def test_every_lead_belongs_to_exactly_one_kind():
    # 새 값을 enum 에만 더하고 표에 빠뜨리면 그 값은 어느 일정에도 걸 수 없다.
    for lead in ReminderLead:
        assert fits(lead, all_day=True) != fits(lead, all_day=False), lead


def test_starts_window_never_misses_a_reminder_that_rings_in_range():
    # 발송은 이 범위의 일정만 꺼낸다. 범위 밖으로 빠지는 알림은 영영 울리지 않는다.
    now = datetime(2026, 10, 28, 12, 0, tzinfo=KST)
    earliest, latest = starts_window(now - timedelta(minutes=10), now)
    for lead in ReminderLead:
        all_day = fits(lead, all_day=True)
        for hours in range(-24 * 10, 24 * 10):
            start = now + timedelta(hours=hours, minutes=7)
            rings = remind_at(lead, starts_at=start, all_day=all_day)
            if now - timedelta(minutes=10) <= rings <= now:
                assert earliest <= start <= latest, (lead, start)
