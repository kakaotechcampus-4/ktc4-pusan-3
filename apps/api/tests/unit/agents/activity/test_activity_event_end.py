"""오늘 밤 행사 — 운영 시간 원문에서 끝나는 시각을 읽는다 (#800)."""

from datetime import time

import pytest

from app.agents.activity.events import ended_today


class TestEndedToday:
    @pytest.mark.parametrize(
        ("playtime", "now", "ended"),
        [
            ("10:00~18:00", time(21), True),
            ("10:00~18:00", time(17, 59), False),
            ("화~일 10:00 ~ 18:00, 금토 18:00-22:00", time(21), False),
            ("화~일 10:00 ~ 18:00, 금토 18:00-22:00", time(22, 30), True),
            ("(토) 19:00", time(21), False),  # 시작 시각뿐 — 모른다
            ("", time(23), False),
            ("회차별 상이", time(23), False),
            ("18:00~02:00", time(23), False),  # 자정을 넘는다 — 끝나지 않았다
            ("10:00~24:00", time(23, 30), False),
        ],
    )
    def test_끝_시각이_지났는지(self, playtime, now, ended):
        assert ended_today(playtime, now) is ended
