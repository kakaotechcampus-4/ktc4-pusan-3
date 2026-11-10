import random
from datetime import date, time

import pytest

from app.rules.notice_dates import parse_base_month, parse_notice_when

ANCHOR = date(2026, 10, 27)  # 화요일


@pytest.mark.parametrize(
    ("text", "start"),
    [
        ("11월 3일(화)", date(2026, 11, 3)),
        ("11/3(화)", date(2026, 11, 3)),
        ("11.3.(화)", date(2026, 11, 3)),
        ("2026.11.3.(화)", date(2026, 11, 3)),
        ("2026년 11월 3일 화요일 아님", date(2026, 11, 3)),
        ("26. 11. 3.", date(2026, 11, 3)),
        ("2026-11-03", date(2026, 11, 3)),
        ("내일", date(2026, 10, 28)),
        ("모레까지", date(2026, 10, 29)),
        ("이번 주 금요일", date(2026, 10, 30)),
        ("다음 주 월요일", date(2026, 11, 2)),
    ],
)
def test_시작_날짜를_읽는다(text, start):
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.start == start
    assert when.review is None


def test_연도는_기준일에서_빌리고_해가_바뀌면_내년이다():
    assert parse_notice_when("1월 5일", anchor=date(2026, 12, 20)).start == date(2027, 1, 5)


def test_조금_지난_날짜는_같은_해다():
    # 행사가 끝나고 나가는 정산 안내
    assert parse_notice_when("9월 26일", anchor=ANCHOR).start == date(2026, 9, 26)


def test_요일이_어긋나면_고치지_않고_되묻는다():
    when = parse_notice_when("11월 3일(수)", anchor=ANCHOR)
    assert when.start == date(2026, 11, 3)
    assert "요일" in when.review


def test_요일이_어긋나도_먼_해로_넘겨_맞추지_않는다():
    when = parse_notice_when("11월 3일(수)", anchor=ANCHOR)
    assert when.start.year == 2026


def test_연간_계획의_지난_달은_요일이_맞는_해다():
    when = parse_notice_when("04.15.(수)", anchor=date(2026, 7, 29))
    assert when.start == date(2026, 4, 15)
    assert when.review is None


def test_연초에_받은_공지의_작년_12월은_구제한다():
    when = parse_notice_when("12월 29일(화)", anchor=date(2027, 1, 5))
    assert when.start == date(2026, 12, 29)
    assert when.review is None


@pytest.mark.parametrize(
    ("text", "start_time", "end_time"),
    [
        ("11월 3일(화) 14시까지", time(14), None),
        ("11월 3일 오후 2시", time(14), None),
        ("11월 3일 오전 9시 30분", time(9, 30), None),
        ("11월 3일 2시 반", time(14, 30), None),
        ("2026.9.26.(금) 9:00~12:30", time(9), time(12, 30)),
        ("11월 3일 오후 1시~3시", time(13), time(15)),
    ],
)
def test_시각을_읽는다(text, start_time, end_time):
    when = parse_notice_when(text, anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (start_time, end_time)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("11/9~11/20", date(2026, 11, 9), date(2026, 11, 20)),
        ("11월 9일~20일", date(2026, 11, 9), date(2026, 11, 20)),
        ("11.23.(월)~11.27.(금)", date(2026, 11, 23), date(2026, 11, 27)),
        ("12월 29일 ~ 1월 2일", date(2026, 12, 29), date(2027, 1, 2)),
    ],
)
def test_기간을_읽는다(text, start, end):
    when = parse_notice_when(text, anchor=ANCHOR)
    assert (when.start, when.end) == (start, end)
    assert when.review is None


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("11/23~27", date(2026, 11, 23), date(2026, 11, 27)),
        ("11/23(월)~27(금)", date(2026, 11, 23), date(2026, 11, 27)),
        ("11.23~27", date(2026, 11, 23), date(2026, 11, 27)),
        ("11.23.(월) ~ 27.(금)", date(2026, 11, 23), date(2026, 11, 27)),
        # 이미 되던 것 — 그대로여야 한다
        ("11월 23일~27일", date(2026, 11, 23), date(2026, 11, 27)),
        ("11/28~12/2", date(2026, 11, 28), date(2026, 12, 2)),
    ],
)
def test_끝_날에_일_글자가_없어도_기간이다(text, start, end):
    # "11/23~27" 의 27 을 못 읽으면 끝이 열린 것으로 보고 첫날 하루 일정이 된다
    when = parse_notice_when(text, anchor=ANCHOR)
    assert (when.start, when.end) == (start, end)
    assert when.open_ended is False
    assert when.review is None


def test_일_글자_없는_끝_날도_요일을_검산한다():
    when = parse_notice_when("11/23(월)~27(목)", anchor=ANCHOR)
    assert "요일" in when.review


def test_일_글자_없는_끝_날의_시각은_매일의_시간이다():
    when = parse_notice_when("11/23~27 10:00~12:00", anchor=ANCHOR)
    assert (when.start, when.end) == (date(2026, 11, 23), date(2026, 11, 27))
    assert (when.start_time, when.end_time) == (time(10), time(12))


@pytest.mark.parametrize(
    ("text", "start_time", "end_time"),
    [
        ("11/23 10~12시", time(10), time(12)),
        ("11월 23일 10~12시", time(10), time(12)),
        ("11월 23일 오후 2~4시", time(14), time(16)),
        ("11월 23일 2~4시", time(14), time(16)),
        ("11월 23일 9~11시 30분", time(9), time(11, 30)),
        ("11월 23일 10~1시", time(10), time(13)),
    ],
)
def test_앞_숫자에_시가_없는_시각_범위를_읽는다(text, start_time, end_time):
    # "10~12시" 의 10 을 못 읽으면 12시가 시작이 된다
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.start == date(2026, 11, 23)
    assert (when.start_time, when.end_time) == (start_time, end_time)
    assert when.review is None


@pytest.mark.parametrize("text", ["11. 23 ~ 12시", "2026. 11. 23 ~ 12시", "11월 23 ~ 12시"])
def test_날짜의_숫자를_시_없는_앞_시각으로_읽지_않는다(text):
    # "11. 23 ~ 12시" 의 23 은 날이다. 시각으로 먹으면 날짜가 사라진다 (검토 R5) — 전처럼 읽는다
    when = parse_notice_when(text, anchor=ANCHOR)
    assert (when.start, when.start_time) == (date(2026, 11, 23), time(12))
    assert when.review is None


def test_마침표_날짜_뒤의_시_없는_시각_범위는_읽는다():
    when = parse_notice_when("11.23. 10~12시", anchor=ANCHOR)
    assert (when.start, when.start_time, when.end_time) == (date(2026, 11, 23), time(10), time(12))


def test_앞_숫자에_시가_없는_7시도_기존_규칙대로_읽는다():
    # 끝(9시)이 분명하면 시작은 끝보다 이른 쪽 하나로만 읽힌다 — "7:30 ~ 19:30" 과 같은 규칙
    when = parse_notice_when("11월 23일 7~9시", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(7), time(9))
    # 표시 없는 5시는 17시, 끝의 7시는 시작보다 늦은 쪽 — "6시 30분 ~ 8시 30분" 과 같은 규칙
    evening = parse_notice_when("11월 23일 5~7시", anchor=ANCHOR)
    assert (evening.start_time, evening.end_time) == (time(17), time(19))


def test_일_없는_끝_날과_시_없는_시각_범위를_같이_읽는다():
    when = parse_notice_when("11/23~27 10~12시", anchor=ANCHOR)
    assert (when.start, when.end) == (date(2026, 11, 23), date(2026, 11, 27))
    assert (when.start_time, when.end_time) == (time(10), time(12))


@pytest.mark.parametrize(
    "text", ["11/23 9:30~12", "11/23 10~12", "11/23~3세", "11/23~27명", "11/23~12월"]
)
def test_물결표_뒤의_숫자가_날이_아니면_끝_날로_읽지_않는다(text):
    # 시각 범위 · 나이 · 인원을 끝 날로 읽으면 없던 긴 일정이 생긴다
    assert parse_notice_when(text, anchor=ANCHOR).end is None


@pytest.mark.parametrize("text", ["11/23~2만원", "11/23~3천원", "11/23~5원"])
def test_물결표_뒤의_금액은_끝_날이_아니다(text):
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.end is None
    assert when.review is None


@pytest.mark.parametrize(
    ("text", "start", "start_time"),
    [
        ("11/2 9:30~12", date(2026, 11, 2), time(9, 30)),
        ("11.2 9:30~12", date(2026, 11, 2), time(9, 30)),
        ("2026.11.2 10:00~11", date(2026, 11, 2), time(10)),
        ("11/2 10시 ~ 12", date(2026, 11, 2), time(10)),
        ("11/2 13:00~15", date(2026, 11, 2), time(13)),
        ("11/2 오후 2시~4", date(2026, 11, 2), time(14)),
        ("11/10 9:30~12", date(2026, 11, 10), time(9, 30)),
        ("11/23 9:30~12", date(2026, 11, 23), time(9, 30)),
    ],
)
def test_시각_뒤의_물결표는_날짜_바로_뒤가_아니다(text, start, start_time):
    # 시각을 지운 자리의 공백까지 날짜 표현으로 치면 "9:30~12" 의 12 가 11/12 가 된다 (검토 R1)
    when = parse_notice_when(text, anchor=ANCHOR)
    assert (when.start, when.start_time, when.end) == (start, start_time, None)
    assert when.review is None


@pytest.mark.parametrize(
    "text", ["11월 중", "11월 말 예정", "시간은 개별 안내", "추후 공지", "9:30", "2월 30일"]
)
def test_못_읽으면_값을_비우고_이유를_준다(text):
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.start is None
    assert when.review


def test_빈_값은_확인거리가_아니다():
    assert parse_notice_when(None, anchor=ANCHOR).review is None
    assert parse_notice_when("  ", anchor=ANCHOR).review is None


def test_괄호_없는_요일도_검산한다():
    assert parse_notice_when("11월 6일 금요일 오전", anchor=ANCHOR).review is None
    assert "요일" in parse_notice_when("11월 6일 목요일", anchor=ANCHOR).review


def test_괄호_없는_한_글자는_요일이_아니다():
    assert parse_notice_when("11월 6일 화상 회의", anchor=ANCHOR).review is None


def test_월간표의_날짜는_기준_월로_읽는다():
    when = parse_notice_when("2일(월)", anchor=ANCHOR, base_month=parse_base_month("11월 행사"))
    assert when.start == date(2026, 11, 2)
    assert when.review is None


def test_월간표의_기간도_읽는다():
    when = parse_notice_when(
        "16일(월) ~ 18일(수)", anchor=ANCHOR, base_month=parse_base_month("2026년 11월")
    )
    assert (when.start, when.end) == (date(2026, 11, 16), date(2026, 11, 18))


def test_월간표의_요일이_어긋나면_되묻는다():
    when = parse_notice_when("2일(수)", anchor=ANCHOR, base_month=(11, 2026))
    assert "요일" in when.review


@pytest.mark.parametrize("base_month", [(11, 2026), (11, None)])
def test_월간표의_일_없는_날짜도_요일이_맞으면_읽는다(base_month):
    # "30(월) 자체 휴원" — 월간표는 "일" 없이 날짜에 요일 괄호만 붙인다
    when = parse_notice_when("30(월)", anchor=ANCHOR, base_month=base_month)
    assert when.start == date(2026, 11, 30)
    assert when.review is None


def test_월간표의_일_없는_기간도_읽는다():
    when = parse_notice_when("16(월) ~ 18(수)", anchor=ANCHOR, base_month=(11, 2026))
    assert (when.start, when.end) == (date(2026, 11, 16), date(2026, 11, 18))
    assert when.review is None


@pytest.mark.parametrize(
    "text", ["2~6(금)", "16~20(금) 김장", "9~13(금)", "1~2(월)", "2~6(금) 10:00~12:00", "2 ~ 6(금)"]
)
def test_월간표의_물결표_뒤_요일_날짜는_혼자_읽지_않는다(text):
    # "2~6(금)" 의 6(금) 만 읽으면 기간이 금요일 하루가 된다 (검토 R2). 전처럼 되묻는다
    when = parse_notice_when(text, anchor=ANCHOR, base_month=(11, 2026))
    assert when.start is None
    assert when.review


@pytest.mark.parametrize("text", ["제3(화)", "만 3(화)", "약3(화)"])
def test_월간표의_낱말에_붙은_숫자는_요일이_맞아도_날짜로_읽지_않는다(text):
    # "제3" 은 차례, "만 3" 은 나이다. 요일이 우연히 맞아도 날짜로 읽지 않고 전처럼 되묻는다
    when = parse_notice_when(text, anchor=ANCHOR, base_month=(11, 2026))
    assert when.start is None
    assert when.review


def test_월간표의_일_없는_날짜는_요일이_어긋나면_읽지_않는다():
    # 숫자만으로는 날짜인지 모른다. 요일이 맞아야 날짜로 본다 — 어긋나면 전처럼 되묻는다
    when = parse_notice_when("30(화)", anchor=ANCHOR, base_month=(11, 2026))
    assert when.start is None
    assert "읽지 못했어요" in when.review


def test_기준_월이_없으면_일_없는_요일_날짜를_읽지_않는다():
    when = parse_notice_when("30(월)", anchor=ANCHOR)
    assert when.start is None
    assert when.review


def test_달력_칸의_숫자는_기준_월의_날짜다():
    when = parse_notice_when("3", anchor=ANCHOR, base_month=(11, None))
    assert when.start == date(2026, 11, 3)
    assert when.review is None


def test_기준_월이_없으면_숫자를_날짜로_읽지_않는다():
    assert parse_notice_when("3", anchor=ANCHOR).start is None


def test_달력_칸의_없는_날은_되묻는다():
    assert parse_notice_when("31", anchor=ANCHOR, base_month=(11, None)).start is None


def test_기준_월이_없으면_달_없는_날짜를_짐작하지_않는다():
    when = parse_notice_when("2일(월)", anchor=ANCHOR)
    assert when.start is None
    assert when.review


def test_기준_월을_못_읽으면_없는_것이다():
    assert parse_base_month("행사 안내") is None
    assert parse_base_month("13월") is None
    assert parse_base_month(None) is None


def test_시각_범위의_물결표를_날짜_범위로_읽지_않는다():
    when = parse_notice_when("19일 10:00~11:30, 27일", anchor=ANCHOR, base_month=(11, None))
    assert when.start == date(2026, 11, 19)
    assert when.end is None
    assert "여러 개" in when.review


@pytest.mark.parametrize("text", ["4일, 5일, 6일", "11월 4일, 11월 18일", "11/4 · 11/18"])
def test_흩어진_날짜는_하나로_뭉개지_않고_되묻는다(text):
    when = parse_notice_when(text, anchor=ANCHOR, base_month=(11, None))
    assert when.end is None
    assert "여러 개" in when.review


def test_기간_뒤의_시각은_그대로_읽는다():
    when = parse_notice_when("11월 9일~11월 20일 10:00~12:00", anchor=ANCHOR)
    assert (when.start, when.end) == (date(2026, 11, 9), date(2026, 11, 20))
    assert (when.start_time, when.end_time) == (time(10), time(12))


def test_분_글자가_없어도_분을_읽는다():
    when = parse_notice_when("11월 2일, 11시30~13시", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(11, 30), time(13))


def test_회차를_범위로_잇지_않는다():
    when = parse_notice_when("11월 2일 오전 10시 30분, 오후 12시 30분, 오후 2시", anchor=ANCHOR)
    assert when.start_time == time(10, 30)
    assert when.end_time is None
    assert "시각이 여러 개" in when.review


@pytest.mark.parametrize(
    "text", ["11/23 10~12시, 2~4시", "11/23 10시~12시, 2시~4시", "11/23 10:00~12:00, 14:00~16:00"]
)
def test_시각_범위가_둘이면_되묻는다(text):
    # 2회차가 조용히 사라지면 안 된다 (검토 R3)
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.start == date(2026, 11, 23)
    assert "시각이 여러 개" in when.review


@pytest.mark.parametrize(
    "text", ["11/23~27 10~12시, 2~4시", "11월 23일~27일 10:00~12:00, 14:00~16:00"]
)
def test_기간_뒤의_시각_범위가_둘이어도_되묻는다(text):
    # 끝 날 쪽의 시각만 있을 때 여러 개인지를 버리면 둘째 회차가 사라진다
    assert "시각이 여러 개" in parse_notice_when(text, anchor=ANCHOR).review


def test_범위_뒤의_괄호_속_시각은_범위가_아니다():
    when = parse_notice_when("2026.9.25.(금) 9:00~12:30(9:30 출발, 12:30 도착)", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(9), time(12, 30))
    assert when.review is None


def test_어떤_글자가_와도_예외를_내지_않는다():
    rng = random.Random(1)
    pieces = [
        "0", "13", "31", "99", "2026", "0000", "9999", "년", "월", "일", "시", "분", ".", "/", "-",
        "~", "(", ")", "(수)", "수요일", "오전", "오후", "주", "내일", " ", ",", "\x00", "🎉",
    ]  # fmt: skip
    for _ in range(20_000):
        text = "".join(rng.choices(pieces, k=rng.randint(1, 9)))
        anchor = date(rng.randint(2000, 2030), rng.randint(1, 12), rng.randint(1, 28))
        parse_notice_when(text, anchor=anchor, base_month=parse_base_month(text))


def test_시각은_자기_쪽_날짜에_붙는다():
    when = parse_notice_when("10월 27일 10:00 ~ 11월 17일 18:00", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(10), time(18))


def test_끝_날의_시각을_시작에_붙이지_않는다():
    when = parse_notice_when("2026.12.27 ~ 2027.1.10 15:00", anchor=ANCHOR)
    assert when.start_time is None
    assert when.end_time == time(15)


def test_끝이_열린_범위를_표시한다():
    assert parse_notice_when("10월 1일 10:00 ~ 선착순마감", anchor=ANCHOR).open_ended is True
    assert parse_notice_when("11월 3일 10시부터", anchor=ANCHOR).open_ended is True
    assert parse_notice_when("11월 3일 ~ 11월 5일", anchor=ANCHOR).open_ended is False


def test_까지는_끝이다():
    assert parse_notice_when("2027년 4월 30일까지 시행", anchor=ANCHOR).until is True
    assert parse_notice_when("11월 3일(화) 14시까지", anchor=ANCHOR).until is True
    assert parse_notice_when("11월 3일 ~ 11월 5일까지", anchor=ANCHOR).until is False


def test_내일과_모레는_되묻는다():
    when = parse_notice_when("내일과 모레", anchor=ANCHOR)
    assert "여러 개" in when.review


def test_물결표_바로_뒤가_아닌_날짜는_끝이_아니다():
    when = parse_notice_when("9.22~ / 9.29~ / 4월 30일까지", anchor=date(2026, 9, 1))
    assert when.start == date(2026, 9, 22)
    assert when.end is None
    assert "여러 개" in when.review


def test_회차_번호를_분으로_읽지_않는다():
    when = parse_notice_when("11월 2일 1부 10시 30분 ~ 17시 2부 17시 ~ 18시 50분", anchor=ANCHOR)
    assert when.start_time == time(10, 30)
    assert when.end_time == time(17)


@pytest.mark.parametrize(
    "text", ["11월 내", "11월 내로", "11월 말까지", "가급적 11월 중", "11월 안에", "11월 말일까지"]
)
def test_달로_적힌_마감은_그달_말일로_읽는다(text):
    when = parse_notice_when(text, anchor=ANCHOR, deadline=True)
    assert when.start == date(2026, 11, 30)
    assert when.review is None


def test_달로_적힌_일시는_마감이_아니면_되묻는다():
    assert parse_notice_when("11월 중", anchor=ANCHOR).start is None


def test_중순은_말일로_읽지_않는다():
    when = parse_notice_when("11월 중순까지", anchor=ANCHOR, deadline=True)
    assert when.start is None
    assert when.review


def test_연말_공지의_다음_달_마감은_다음_해다():
    when = parse_notice_when("1월 내", anchor=date(2026, 12, 20), deadline=True)
    assert when.start == date(2027, 1, 31)


def test_윤년_2월_말일을_맞춘다():
    when = parse_notice_when("2월 말까지", anchor=date(2028, 1, 10), deadline=True)
    assert when.start == date(2028, 2, 29)


def test_지난_달_마감은_다음_해로_넘기지_않는다():
    when = parse_notice_when("9월 내", anchor=date(2026, 11, 2), deadline=True)
    assert when.start == date(2026, 9, 30)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11월 6일 3시까지", time(15, 0)),
        ("11월 6일 1:30", time(13, 30)),
        ("11월 6일 6시", time(18, 0)),
        ("11월 6일 오전 3시", time(3, 0)),
        ("11월 6일 12시", time(12, 0)),
    ],
)
def test_오전_오후가_없는_이른_시각은_오후다(text, expected):
    assert parse_notice_when(text, anchor=ANCHOR).start_time == expected


@pytest.mark.parametrize("text", ["11월 6일 7시", "11월 6일 7시 30분까지", "11월 6일 7:30"])
def test_아침도_저녁도_되는_시각은_짐작하지_않고_날짜만_남긴다(text):
    # 운영 시간 안에서 7시는 등원 시각도 저녁 모임 시각도 된다
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.start == date(2026, 11, 6)
    assert when.start_time is None
    assert when.vague_time is True
    assert when.review is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11월 6일 저녁 7시", time(19, 0)),
        ("11월 6일 아침 7시 30분", time(7, 30)),
        ("11월 6일 07:30", time(7, 30)),
        ("11월 6일 새벽 5시", time(5, 0)),
        ("11월 6일 밤 9시", time(21, 0)),
        ("11월 6일 밤 12시", time(23, 59)),
        ("11월 6일 낮 1시", time(13, 0)),
    ],
)
def test_시각_앞의_낱말로_오전_오후를_안다(text, expected):
    when = parse_notice_when(text, anchor=ANCHOR)
    assert when.start_time == expected
    assert when.vague_time is False


@pytest.mark.parametrize(
    ("text", "start_time", "end_time"),
    [
        ("11월 6일 오전 9시~12시", time(9), time(12)),
        ("11월 6일 오전 10~12시", time(10), time(12)),
        ("11월 6일 오전 10:00~12:00", time(10), time(12)),
        ("11월 6일 오전 11시~오후 1시", time(11), time(13)),
    ],
)
def test_이어받은_오전은_12시를_자정으로_만들지_않는다(text, start_time, end_time):
    # "오전 9시~12시" 의 12시는 정오다. 앞의 "오전" 을 이어받으면 00:00 이 된다 (검토 R4)
    when = parse_notice_when(text, anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (start_time, end_time)


def test_이어받은_오전의_12시는_기간의_끝_날에도_정오다():
    when = parse_notice_when("11/23~11/24 오전 10~12시", anchor=ANCHOR)
    assert (when.start, when.end) == (date(2026, 11, 23), date(2026, 11, 24))
    assert (when.start_time, when.end_time) == (time(10), time(12))


def test_바로_붙은_오전_12시는_그대로_자정이다():
    assert parse_notice_when("11월 6일 오전 12시", anchor=ANCHOR).start_time == time(0)


def test_끝이_7시면_시작보다_늦은_쪽으로_읽는다():
    when = parse_notice_when("11월 6일 10시 ~ 7시", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(10, 0), time(19, 0))


def test_끝_시각이_시작보다_이르면_오후로_읽는다():
    when = parse_notice_when("11월 6일 6시 30분 ~ 8시 30분", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(18, 30), time(20, 30))


def test_시각_범위는_오전에서_오후로_이어진다():
    when = parse_notice_when("11월 6일 9시 ~ 3시", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(9, 0), time(15, 0))


def test_명시한_시각이_거꾸로면_고치지_않는다():
    when = parse_notice_when("11월 6일 14:00~10:00", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(14, 0), time(10, 0))


def test_끝이_분명하면_7시대_시작도_하나로_읽는다():
    # "7:30 ~ 19:30" 의 시작은 19:30 일 수 없다 — 끝보다 이른 쪽 하나만 남는다 (#726 리뷰)
    when = parse_notice_when("11월 6일 7:30 ~ 19:30", anchor=ANCHOR)
    assert (when.start_time, when.end_time) == (time(7, 30), time(19, 30))
    assert when.vague_time is False
