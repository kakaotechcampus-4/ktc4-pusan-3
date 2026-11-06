"""행사 일정 구절 읽기 — 원문 구절에서 날짜 · 요일 · 장소를 코드로 읽는다(모델 없음).

고정하는 것
- 모델은 구절을 고르기만 하고 요일 · 날짜는 여기서 읽는다. 못 읽는 구절은 읽었다고 하지 않는다
  (`unreadable`) — 모르는 날은 열리지 않는 날이다
- 요일 표기: X요일 · (X) · X~Y · "목~일" 처럼 요일 글자 없는 범위 · 금, 토, 일 · 매주 X ·
  매월 n째 주 · 평일 · 주말 · 시간 앞 요일("토- 13:00")
- 날짜 표기: YYYY.MM.DD · M월 D일 · M.D · 월 없는 D일(앞 날짜의 월, 없으면 기간에 하나뿐인 그날)
- 날짜 뒤 (요일)이 날짜와 안 맞으면 · 격주 · 짝수 · 홀수 · 월을 알 수 없는 날은 읽지 않는다
- 장소는 소개정보 장소 칸의 한 칸만 — 원문 아무 곳의 낱말을 장소로 옮기지 않는다

행사 이름 · 장소 · 원문은 지어낸 것이다.
"""

from datetime import date, timedelta

import pytest

from app.agents.activity.events import Event
from app.agents.activity.schedule_quote import (
    closed_weekdays,
    parse,
    playtime_weekdays,
    reopened,
    uncovered,
    venue_of,
    venue_pairs,
    weekday_cues,
)
from app.agents.activity.schemas.common import EventKind


def event(starts_on: str, ends_on: str) -> Event:
    return Event(
        content_id="1",
        name="별빛 가족 축제",
        kind=EventKind.EVENT,
        starts_on=date.fromisoformat(starts_on),
        ends_on=date.fromisoformat(ends_on),
        lat=35.1,
        lon=129.0,
    )


AUTUMN = event("2026-10-01", "2026-11-30")


def days(*isos: str) -> set[date]:
    return {date.fromisoformat(iso) for iso in isos}


class TestWeekdays:
    @pytest.mark.parametrize(
        ("quote", "weekdays"),
        [
            ("매주 토요일 공연", {5}),
            ("10:00~17:00 (목~일)", {3, 4, 5, 6}),
            ("토-일 운영", {5, 6}),
            ("목~일요일", {3, 4, 5, 6}),
            ("화요일~일요일", {1, 2, 3, 4, 5, 6}),
            ("금·토 10:00~18:00", {4, 5}),
            ("월~금 10:00~18:00", {0, 1, 2, 3, 4}),
            ("매주 금, 토, 일", {4, 5, 6}),
            ("평일 19:30", {0, 1, 2, 3, 4}),
            ("주말 15:00", {5, 6}),
            ("토- 13:00~22:00일- 11:00~21:00", {5, 6}),
            ("토~월", {5, 6, 0}),
        ],
    )
    def test_요일_표기를_읽는다(self, quote, weekdays):
        quoted = parse(quote, AUTUMN)
        assert not quoted.unreadable
        assert quoted.weekdays == weekdays

    @pytest.mark.parametrize("quote", ["1일~3일", "09:00~18:00", "9월 1일 - 10:00 개막"])
    def test_날짜와_시각은_요일로_읽지_않는다(self, quote):
        assert parse(quote, AUTUMN).weekdays == set()

    def test_매월_몇째_주_요일을_읽는다(self):
        quoted = parse("매월 둘째 주와 넷째 주 토요일마다", AUTUMN)
        assert quoted.monthly == [(2, 5), (4, 5)]
        window = [date(2026, 11, 1) + timedelta(days=i) for i in range(30)]
        assert quoted.days(window) == days("2026-11-14", "2026-11-28")

    @pytest.mark.parametrize("quote", ["격주 토요일", "짝수 주 일요일", "홀수 달 첫째 주"])
    def test_격주_짝수_홀수는_읽지_않는다(self, quote):
        assert parse(quote, AUTUMN).unreadable


class TestDates:
    def test_날짜_목록에서_앞_날짜의_월을_잇고_작아지면_다음_달이다(self):
        quoted = parse("10월 23일(금), 24일(토), 6일(금)", AUTUMN)
        assert quoted.dates == days("2026-10-23", "2026-10-24", "2026-11-06")

    @pytest.mark.parametrize(
        ("quote", "expected"),
        [
            ("2026.11.07.(토)", {"2026-11-07"}),
            ("11.07~11.08 공연", {"2026-11-07", "2026-11-08"}),
            ("11/7, 11/8", {"2026-11-07", "2026-11-08"}),
            ("11월 28일부터", {"2026-11-28", "2026-11-29", "2026-11-30"}),
            ("10월 3일까지", {"2026-10-01", "2026-10-02", "2026-10-03"}),
        ],
    )
    def test_날짜_표기를_읽는다(self, quote, expected):
        assert parse(quote, AUTUMN).dates == days(*expected)

    def test_날짜와_요일을_같이_적었으면_둘_다_맞는_날이다(self):
        quoted = parse("11월 1일부터 15일까지 매주 토요일", AUTUMN)
        window = [date(2026, 11, 1) + timedelta(days=i) for i in range(30)]
        assert quoted.days(window) == days("2026-11-07", "2026-11-14")

    def test_월_없는_날은_기간에_그날이_하나뿐이면_그_달이다(self):
        short = event("2026-10-10", "2026-10-31")
        quoted = parse("10일부터 31일까지 매주 토요일, 총 4회", short)
        window = [date(2026, 10, 10) + timedelta(days=i) for i in range(22)]
        assert quoted.days(window) == days("2026-10-10", "2026-10-17", "2026-10-24", "2026-10-31")

    def test_월_없는_날이_기간에_여럿이면_읽지_않는다(self):
        assert parse("10일부터 31일까지", AUTUMN).unreadable

    def test_날짜_뒤_요일이_날짜와_안_맞으면_읽지_않는다(self):
        # 2026-10-18 은 일요일이다
        assert parse("2026.10.18.(금) 10:00~15:00", AUTUMN).unreadable

    def test_기간_밖_날짜만_적은_구절은_기간_밖이다(self):
        quoted = parse("11월 13일부터 15일까지", event("2026-10-30", "2026-11-01"))
        assert quoted.outside
        assert quoted.dates == set()

    @pytest.mark.parametrize("quote", ["주 6일 운영", "3일간 열린다"])
    def test_날짜가_아닌_일은_날짜로_읽지_않는다(self, quote):
        quoted = parse(quote, AUTUMN)
        assert not quoted.unreadable
        assert quoted.dates == set()


class TestClosed:
    def test_쉬는_요일을_원문_어디서든_찾는다(self):
        text = "전시는 기간 내내 열린다. 휴관일: 매주 월요일. 화요일은 정기 휴무"
        assert closed_weekdays(text) == {0, 1}

    def test_쉬는_날_구절의_날짜_제외는_그날을_연다(self):
        text = "휴관일: 매주 월요일(2026.11.02 제외)"
        assert reopened(text, AUTUMN) == days("2026-11-02")

    def test_쉬는_날과_상관없는_제외는_열지_않는다(self):
        assert reopened("입장료 무료(2026.11.02 제외)", AUTUMN) == set()

    def test_쉬는_날_구절은_날짜와_요일_중_하나만_맞아도_쉰다(self):
        # 여는 날 구절은 둘 다 맞는 날이지만("11월 1~15일 중 토요일"), 쉬는 날은 둘 다다
        quoted = parse("매주 수요일 및 11/14~11/16 행사 미진행", AUTUMN)
        window = [date(2026, 11, 9) + timedelta(days=i) for i in range(14)]
        assert quoted.any_days(window) == days(
            "2026-11-11", "2026-11-14", "2026-11-15", "2026-11-16", "2026-11-18"
        )

    @pytest.mark.parametrize(
        "text", ["매주 수요일 미진행", "매주 수요일 행사 미진행", "수요일은 공연 없음"]
    )
    def test_미진행도_쉬는_날이다(self, text):
        assert closed_weekdays(text) == {2}


class TestCues:
    def test_요일_제한이_적힌_자리를_찾는다(self):
        text = "운영 시간 10:00~17:00 (목~일). 매주 토요일 공연, 매월 둘째 주 일요일 장터"
        assert [cue for cue in weekday_cues(text)] == ["목~일", "매주 토요일", "매월"]

    def test_구절이_덮지_않은_요일_자리를_돌려준다(self):
        text = "평일 10:00~18:00. 매주 토요일 공연. 매주 일요일 장터"
        assert uncovered(text, ["매주 토요일 공연"]) == ["매주 일요일"]

    def test_날짜가_적힌_자리도_덮어야_한다(self):
        text = "기간 내내 전시. 11.07~11.08 공연"
        assert uncovered(text, []) == ["11.07", "11.08"]
        assert uncovered(text, ["11.07~11.08 공연"]) == []

    @pytest.mark.parametrize("text", ["산책로 3.2km 구간", "10:00~18:00", "1.5배 크기"])
    def test_수치와_시각은_날짜_자리가_아니다(self, text):
        assert uncovered(text, []) == []


class TestPlaytime:
    @pytest.mark.parametrize(
        ("playtime", "weekdays"),
        [
            ("10:00~17:00 (목~일)", {3, 4, 5, 6}),
            ("매주 토요일 15:00~16:00", {5}),
            ("화요일~일요일 10:00~18:00", {1, 2, 3, 4, 5, 6}),
            ("[토요일] 11:00 · [일요일] 14:00", {5, 6}),
            ("(토) 11:00~15:00", {5}),
            ("10:00~18:00", set()),
            # 시간만 다른 요일 · 쉬는 요일은 여는 요일이 아니다
            ("10:00~18:00 (토요일은 21:00까지)", set()),
            ("10:00~18:00 (월요일 휴관)", set()),
            ("매주 월요일 휴무", set()),
            ("19:00~22:30 ※ 매주 수요일 및 10/14~10/22 행사 미진행", set()),
            # 일곱 요일이 다 나오면 제한이 아니다
            ("월~일 10:00~18:00", set()),
        ],
    )
    def test_운영_시간_칸의_여는_요일(self, playtime, weekdays):
        assert playtime_weekdays(playtime) == weekdays


class TestVenue:
    PLACE = "물빛호수공원 / 은행나무공원 / 별마루광장"

    def test_구절에_장소_칸이_하나만_있으면_그_칸이다(self):
        assert venue_of("2026.11.07.(토) - 장소 : 물빛호수공원", self.PLACE) == "물빛호수공원"

    def test_장소_칸에_없는_이름은_장소가_아니다(self):
        assert venue_of("11월 7일 시청 앞 광장", self.PLACE) is None

    def test_날짜_뒤에_장소가_오는_목록(self):
        text = (
            "- 기간 : 2026.11.07.(토) - 장소 : 물빛호수공원(가람구) "
            "- 기간 : 2026.11.14.(토) - 장소 : 은행나무공원(나루구) "
            "- 기간 : 2026.11.21.(토) - 장소 : 별마루광장"
        )
        quotes = ["2026.11.07.(토)", "2026.11.14.(토)", "2026.11.21.(토)"]
        assert venue_pairs(quotes, text, self.PLACE) == {
            "2026.11.07.(토)": "물빛호수공원",
            "2026.11.14.(토)": "은행나무공원",
            "2026.11.21.(토)": "별마루광장",
        }

    def test_장소_뒤에_날짜가_묶인_목록(self):
        text = "물빛호수공원 11월 7일, 11월 14일 은행나무공원 11월 21일"
        quotes = ["11월 7일", "11월 14일", "11월 21일"]
        assert venue_pairs(quotes, text, self.PLACE) == {
            "11월 7일": "물빛호수공원",
            "11월 14일": "물빛호수공원",
            "11월 21일": "은행나무공원",
        }

    def test_앞뒤_어느_쪽으로도_짝이_맞으면_짝짓지_않는다(self):
        # 날짜마다 앞에도 뒤에도 장소가 하나씩 있다 — 어느 쪽인지 원문으로 알 수 없다
        text = "물빛호수공원 11월 7일 은행나무공원 11월 14일 별마루광장"
        assert venue_pairs(["11월 7일", "11월 14일"], text, self.PLACE) == {}

    def test_장소_칸이_하나면_짝짓지_않는다(self):
        assert venue_pairs(["11월 7일"], "11월 7일 물빛호수공원", "물빛호수공원") == {}
