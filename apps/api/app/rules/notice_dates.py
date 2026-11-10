"""기관 공지의 날짜 표현 → 확정 날짜. 모델에게 묻지 않는다 (CLAUDE.md §2 · §3).

공지의 날짜에는 대개 연도가 없다 — "9월 17일(수) 14시까지". 그래서 기준일(발행일, 없으면 받은 날)
에서 연도를 빌리고, 적혀 있는 요일이 맞는지 검산한다. 요일이 어긋나면 잘못 읽은 것이므로 고치지
않고 `review` 로 돌려준다. 못 읽은 표현을 오늘로 채우지 않는다.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from itertools import pairwise
from typing import NamedTuple

_WEEKDAYS = "월화수목금토일"

# "11월 1주" 는 11월 1일이 아니다. 조용히 틀린 날짜가 초안에 오르는 것이 못 읽는 것보다 나쁘다
_NOT_A_DAY = r"(?!\s*(?:주|째|차|회|개|명|번|학|세|살|%|시|분|달))"
_YMD = (
    r"(?P<y>\d{4}|\d{2})\s*(?:년|[./-])\s*(?P<m>\d{1,2})\s*(?:월|[./-])\s*(?P<d>\d{1,2})"
    + _NOT_A_DAY
    + r"\s*일?\.?"
)
_MD = r"(?P<m>\d{1,2})\s*(?:월|[./])\s*(?P<d>\d{1,2})" + _NOT_A_DAY + r"\s*일?\.?"
# "(금)" · "(금요일)" · "금요일". 괄호 없는 한 글자는 요일이 아니다 — "8월 8일 화상 회의"
_DOW = (
    r"(?:\s*[(（]\s*(?P<w>[월화수목금토일])\s*(?:요일)?\s*[)）]"
    r"|\s*(?P<w2>[월화수목금토일])요일)"
)
_FULL = re.compile(_YMD + f"(?:{_DOW})?")
_SHORT = re.compile(r"(?<![\d.])" + _MD + f"(?:{_DOW})?")
_BARE_DAY = re.compile(r"(?<![\d.])(?P<d>\d{1,2})\s*일" + f"(?:{_DOW})?")
# "11/23~27" · "11.23(월)~27(금)" — 기간의 끝 날은 "일" 도 빠진다.
# 뒤에 숫자 · 달 · 단위가 오면 날이 아니다
_BARE_END_DAY = re.compile(
    r"(?P<d>\d{1,2})(?!\s*[\d:/]|\.\d|\s*(?:월|년))" + _NOT_A_DAY + r"\.?" + f"(?:{_DOW})?"
)
_CALENDAR_CELL = re.compile(r"\d{1,2}")
# 월간표의 "30(월)" — "일" 없이 요일 괄호만 붙는다. 숫자만으로는 날인지 몰라 요일이 맞을 때만 읽는다
_CELL_WITH_DOW = re.compile(
    r"(?<![\d./:])(?P<d>\d{1,2})\s*[(（]\s*(?P<w>[월화수목금토일])\s*(?:요일)?\s*[)）]"
)
# "2~6(금)" 의 6(금) 은 기간의 끝이다. 혼자 읽으면 기간이 금요일 하루가 된다
_ENDS_WITH_RANGE_SEP = re.compile(r"(?:~|∼|〜|-|–|—|부터)\s*$")
_BASE_MONTH = re.compile(r"(?:(?P<y>\d{4})\s*년\s*)?(?P<m>\d{1,2})\s*월")
_RELATIVE = {"오늘": 0, "금일": 0, "내일": 1, "명일": 1, "모레": 2}
_WEEK_WORD = re.compile(r"(?P<week>이번\s*주|금주|다음\s*주|차주)\s*(?P<wk>[월화수목금토일])요일")
# 시각 앞의 낱말이 오전 · 오후를 알려 준다. "저녁 7시" 를 아침 7시로 읽으면 안 된다
_MERIDIEM = r"(?P<mer>새벽|아침|오전|낮|오후|저녁|밤)?"
_MORNING = frozenset({"새벽", "아침", "오전"})
_CLOCK = re.compile(_MERIDIEM + r"\s*(?P<h>\d{1,2})\s*:\s*(?P<min>\d{2})")
_HANGUL_TIME = re.compile(
    _MERIDIEM + r"\s*(?P<h>\d{1,2})\s*시"
    r"(?:\s*(?P<min>\d{1,2})\s*분|(?P<min2>\d{2})(?!\d)|\s*(?P<half>반))?"
)
# "10~12시" · "오후 2~4시" — 범위의 앞 시각은 "시" 가 빠진다. "~" 뒤에 "N시" 가 바로 올 때만
# 시각이다. 오전 · 오후는 다른 시각과 같은 규칙으로 읽는다(_times · _time_span).
# 앞에 숫자 · 구분 기호가 붙은 숫자는 날짜의 일부다 ("11/23~12시" 의 23)
_BARE_HOUR = re.compile(
    _MERIDIEM
    + r"\s*(?<![\d./:\-])(?P<h>\d{1,2})(?P<min>)"
    + r"(?=\s*(?:~|∼|〜|-|–|—)\s*(?:새벽|아침|오전|낮|오후|저녁|밤)?\s*\d{1,2}\s*시(?!간))"
)
_TIME_RANGE = re.compile(r"\0+\s*(?:~|∼|〜|-|–|—|부터)\s*\0+")
_UNTIL = re.compile(r"\s*까지")
_DATE_START = re.compile(r"\s*(?:\d|오늘|금일|내일|명일|모레|이번|금주|다음|차주)")
_RANGE_SEP = re.compile(r"\s*(?:~|∼|〜|-|–|—|부터)\s*")
_MONTH_ONLY = re.compile(r"\d{1,2}\s*월(?:\s*(?:중|초|말|중순|예정))?")
_MONTH_DEADLINE = re.compile(r"(?P<m>\d{1,2})\s*월\s*(?:말|내|안|중(?!순))")

# 오전 · 오후가 안 적힌 시각은 어린이집 운영 시간(7:30 ~ 19:30) 안에서 한 가지로 읽힐 때만 받는다.
# 1 ~ 6시는 13 ~ 18시로만 읽힌다 — 새벽은 기관 일과에 없다. 7시는 아침도 저녁도 되어 짐작하지 않는다
_EARLY_HOUR = 6
_BOTH_WAYS = frozenset({7})
# 발행일보다 이만큼 앞선 날짜는 같은 해의 과거로 본다(정산 안내 · 지난 행사). 더 앞서면 내년이다
_PAST_WINDOW = timedelta(days=60)
# 적힌 요일을 믿고 연도를 고를 때 기준일에서 벗어날 수 있는 범위
_WEEKDAY_WINDOW = timedelta(days=300)


@dataclass(frozen=True)
class NoticeWhen:
    start: date | None = None
    start_time: time | None = None
    end: date | None = None
    end_time: time | None = None
    review: str | None = None  # 값이 있으면 보호자 확인이 필요한 이유
    open_ended: bool = False  # "10월 1일 ~ 선착순 마감" · "3시부터" — 시작만 있고 끝을 모른다
    until: bool = False  # "4월 30일까지" — 이 날짜는 시작이 아니라 끝이다
    vague_time: bool = False  # "7시" — 오전인지 오후인지 몰라 시각을 비웠다. 날짜는 남긴다


class _Clock(NamedTuple):
    at: time
    start: int  # 글 안의 위치
    end: int
    guessed: bool  # 오전 · 오후가 안 적혀 오후로 읽었다 ("3시")
    vague: bool  # 오전 · 오후가 안 적혔고 둘 다 될 수 있다 ("7시")


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _nearest(month: int, day: int, anchor: date) -> date | None:
    """연도 없는 월 · 일 → 기준일 뒤쪽을 먼저 보고, 지난 날짜는 _PAST_WINDOW 까지만 허용한다."""
    this_year = _safe(anchor.year, month, day)
    if this_year is None:
        return _safe(anchor.year + 1, month, day)  # 2월 29일
    if this_year >= anchor - _PAST_WINDOW:
        return this_year
    return _safe(anchor.year + 1, month, day) or this_year


def _written_weekday(m: re.Match) -> str | None:
    return m["w"] or m["w2"]


def _dow_mismatch(found: date, written: str | None) -> bool:
    return written is not None and _WEEKDAYS[found.weekday()] != written


def _month_day(
    month: int, day: int, written: str | None, anchor: date, phrase: str, end: int
) -> tuple[date | None, str | None, int]:
    """연도 없는 월 · 일. 적힌 요일이 있으면 그 요일이 맞는 해를 고른다."""
    found = _nearest(month, day, anchor)
    if found is None:
        return None, f"없는 날짜예요: {phrase}", end
    if not _dow_mismatch(found, written):
        return found, None, end
    # 연간 계획은 지난 달을 싣고, 연말 공지는 다음 해를 싣는다. 기준일에서 먼 해로 넘겨 맞추면
    # 잘못 읽은 날짜가 조용히 저장되므로 범위를 묶는다
    for year in (anchor.year, anchor.year - 1, anchor.year + 1):
        other = _safe(year, month, day)
        if other and abs(other - anchor) <= _WEEKDAY_WINDOW and not _dow_mismatch(other, written):
            return other, None, end
    return found, f"요일이 달라요: {phrase}", end


def _first_date(
    text: str, anchor: date, base_month: int | None
) -> tuple[date | None, str | None, int]:
    """(날짜, 확인 이유, 그 표현이 끝난 위치)."""
    if m := _FULL.search(text):
        year = int(m["y"]) + (2000 if len(m["y"]) == 2 else 0)
        found = _safe(year, int(m["m"]), int(m["d"]))
        if found is None:
            return None, f"없는 날짜예요: {m[0].strip()}", m.end()
        if _dow_mismatch(found, _written_weekday(m)):
            return found, f"요일이 달라요: {m[0].strip()}", m.end()
        return found, None, m.end()
    if m := _SHORT.search(text):
        return _month_day(
            int(m["m"]), int(m["d"]), _written_weekday(m), anchor, m[0].strip(), m.end()
        )
    if base_month is not None and (m := _BARE_DAY.search(text)):
        return _month_day(
            base_month, int(m["d"]), _written_weekday(m), anchor, m[0].strip(), m.end()
        )
    if (
        base_month is not None
        and (m := _CELL_WITH_DOW.search(text))
        and not _ENDS_WITH_RANGE_SEP.search(text[: m.start()])
        and (found := _nearest(base_month, int(m["d"]), anchor))
        and not _dow_mismatch(found, m["w"])
    ):
        # 요일이 어긋나면 다른 해로 옮겨 맞추지 않는다 — 읽지 못한 것으로 둔다
        return found, None, m.end()
    if m := _WEEK_WORD.search(text):
        monday = anchor - timedelta(days=anchor.weekday())
        if m["week"].replace(" ", "") in ("다음주", "차주"):
            monday += timedelta(days=7)
        return monday + timedelta(days=_WEEKDAYS.index(m["wk"])), None, m.end()
    for word, days in _RELATIVE.items():
        if (at := text.find(word)) >= 0:
            return anchor + timedelta(days=days), None, at + len(word)
    return None, None, 0


def _bare_hours(text: str) -> list[re.Match]:
    """시 없는 앞 시각. 날짜 표현 안의 숫자는 뺀다.

    "11. 23 ~ 12시" · "11월 23 ~ 12시" 의 23 은 날이다. 달 · 날로 있을 수 없는 짝은 날짜로 치지
    않는다 — "11.23. 10~12시" 를 "11년 23월 10일" 로 보면 10 이 날이 된다.
    """
    dates = [
        m.span()
        for m in (*_FULL.finditer(text), *_SHORT.finditer(text))
        if 1 <= int(m["m"]) <= 12 and 1 <= int(m["d"]) <= 31
    ]
    return [
        m for m in _BARE_HOUR.finditer(text) if not any(a <= m.start("h") < b for a, b in dates)
    ]


def _time_of(mer: str | None, hour: int, minute: int) -> time | None:
    if mer == "밤" and hour == 12:
        hour = 24  # 밤 12시는 그날의 끝이다
    elif mer is not None and mer not in _MORNING and hour < 12:
        hour += 12
    elif mer is None and 1 <= hour <= _EARLY_HOUR:
        hour += 12  # "3시까지" 는 15시다
    if mer in _MORNING and hour == 12:
        hour = 0
    if hour == 24 and minute == 0:
        return time(23, 59)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _times(text: str) -> list[_Clock]:
    """글에 나온 순서대로."""
    found: list[_Clock] = []
    taken: list[tuple[int, int]] = []
    last_mer: str | None = None
    for m in sorted(
        [*_CLOCK.finditer(text), *_HANGUL_TIME.finditer(text), *_bare_hours(text)],
        key=lambda x: x.start(),
    ):
        if any(a <= m.start() < b for a, b in taken):
            continue
        taken.append(m.span())
        mer = m["mer"] or last_mer
        last_mer = m["mer"] or last_mer
        groups = m.groupdict()
        minute = 30 if groups.get("half") else int(m["min"] or groups.get("min2") or 0)
        hour = int(m["h"])
        if m["mer"] is None and mer in _MORNING and hour == 12:
            # "오전 9시~12시" 의 12시는 정오다. 이어받은 "오전" 으로 자정을 만들지 않는다.
            # 바로 붙은 "오전 12시" 는 적힌 그대로 자정이다
            mer = None
        parsed = _time_of(mer, hour, minute)
        if parsed is not None:
            bare = mer is None and not m["h"].startswith("0")  # "07:30" 은 24시간제다
            found.append(
                _Clock(
                    parsed,
                    m.start(),
                    m.end(),
                    guessed=bare and 1 <= hour <= _EARLY_HOUR,
                    vague=bare and hour in _BOTH_WAYS,
                )
            )
    return found


def _time_span(text: str) -> tuple[time | None, time | None, bool, bool]:
    """(시작, 끝, 시각이 여러 개인가, 오전 · 오후를 몰라 비웠는가). "~" 로 이어져야 범위다.

    "오전 10시 30분, 오후 12시 30분, 오후 2시" 는 범위가 아니라 회차다. 앞의 둘을 잇지 않는다.
    """
    times = _times(text)
    if not times:
        return None, None, False, False
    first, *rest = times
    if rest and _RANGE_SEP.fullmatch(text[first.end : rest[0].start]):
        start, end = first.at, rest[0].at
        # 범위가 또 있으면 회차다 — "10~12시, 2~4시" 의 둘째 회차가 조용히 사라지면 안 된다.
        # 범위 뒤의 낱 시각("9:30 출발, 12:30 도착")은 범위가 아니라 세지 않는다
        several = any(_RANGE_SEP.fullmatch(text[a.end : b.start]) for a, b in pairwise(rest[1:]))
        if first.vague and not rest[0].vague:
            # "7:30 ~ 19:30" — 끝이 분명하면 시작은 끝보다 이른 쪽 하나로만 읽힌다
            fits = [t for t in (start, start.replace(hour=start.hour + 12)) if t < end]
            if len(fits) == 1:
                return fits[0], end, several, False
        if first.vague:
            return None, None, several, True
        if (first.guessed or rest[0].vague) and end <= start and end.hour < 12:
            # 끝이 시작보다 이를 수는 없다 — "6시 30분 ~ 8시 30분" · "10시 ~ 7시" 는 저녁에 끝난다
            return start, end.replace(hour=end.hour + 12), several, False
        if rest[0].vague:
            return start, None, several, True
        return start, end, several, False
    if first.vague:
        return None, None, bool(rest), True
    return first.at, None, bool(rest), False


def _hide(m: re.Match) -> str:
    return "\0" * len(m[0])


def _drop_time_words(text: str) -> str:
    """시각을 지운다 — "9:30" 의 숫자를 날짜로 읽지 않게. 글자 수는 그대로 둔다.

    위치가 같아야 시각이 범위의 어느 쪽에 붙었는지 원문에서 찾을 수 있다.
    시각 범위의 "~" 도 같이 지운다. 남기면 "19일 10:00~11:30, 27일" 이 19일부터 27일까지가 된다.
    """
    # "시" 없는 앞 시각은 뒤의 "N시" 를 보고 찾으므로 그것을 지우기 전에 지운다
    for m in _bare_hours(text):
        text = text[: m.start()] + _hide(m) + text[m.end() :]
    marked = _HANGUL_TIME.sub(_hide, _CLOCK.sub(_hide, text))
    return _TIME_RANGE.sub(lambda m: " " * len(m[0]), marked).replace("\0", " ")


def parse_base_month(text: str | None) -> tuple[int, int | None] | None:
    """월간 일정표의 기준 월 → (월, 연도). 없으면 None."""
    m = _BASE_MONTH.search(text or "")
    if m is None or not 1 <= int(m["m"]) <= 12:
        return None
    return int(m["m"]), int(m["y"]) if m["y"] else None


def _month_end(month: int, anchor: date) -> date | None:
    # 지난 달인지는 그달 끝으로 따진다. 1일로 따지면 이번 달 초의 지난 마감이 내년이 된다
    near_end = _nearest(month, 28, anchor)
    if near_end is None:
        return None
    return near_end.replace(day=calendar.monthrange(near_end.year, month)[1])


def parse_notice_when(
    date_text: str | None,
    *,
    anchor: date,
    base_month: tuple[int, int | None] | None = None,
    deadline: bool = False,
) -> NoticeWhen:
    """날짜 표현 하나를 읽는다. 못 읽으면 값을 비우고 이유를 돌려준다.

    `base_month` 는 월간 일정표용이다. "2일(수)" 처럼 달이 빠진 표현은 이 달로 읽고, 달이 없는데
    이 값도 없으면 짐작하지 않고 되묻는다.

    `deadline` 은 마감 표현이다. "9월 내" 는 언제인지 모르는 일시지만, 마감으로는 그달 말일이다.
    """
    if not date_text or not date_text.strip():
        return NoticeWhen()
    text = date_text.strip()
    # 달력 칸에는 숫자만 적혀 있다. 기준 월을 아는 문서에서만 날짜로 읽는다
    if base_month and _CALENDAR_CELL.fullmatch(text):
        text = f"{text}일"
    dateless = _drop_time_words(text)
    month = base_month[0] if base_month else None
    if base_month and base_month[1]:
        anchor = date(base_month[1], base_month[0], 1)

    start, reason, at = _first_date(dateless, anchor, month)
    if start is None:
        if reason:
            return NoticeWhen(review=reason)
        if (
            deadline
            and (m := _MONTH_DEADLINE.search(dateless))
            and 1 <= int(m["m"]) <= 12
            and (last := _month_end(int(m["m"]), anchor))
        ):
            return NoticeWhen(last, until=True)
        if _MONTH_ONLY.search(dateless):
            return NoticeWhen(review=f"날짜가 정해지지 않았어요: {text}")
        if _times(text):
            return NoticeWhen(review=f"날짜 없이 시각만 있어요: {text}")
        return NoticeWhen(review=f"날짜를 읽지 못했어요: {text}")

    end: date | None = None
    split_at: int | None = None
    open_ended = False
    tail = dateless[at:]
    if sep := _RANGE_SEP.match(tail):
        rest = tail[sep.end() :]
        # 끝 날짜는 "~" 바로 뒤에 온다. "9.22~ / 9.29~ … 4월 30일까지" 의 9.29 는 끝이 아니다
        adjacent = bool(_DATE_START.match(rest))
        end, end_reason, _ = _first_date(rest, start, month) if adjacent else (None, None, 0)
        reason = reason or end_reason
        bare = _BARE_DAY.match(rest.strip()) if end is None else None
        # 날짜 표현 끝의 공백은 지운 시각일 수 있다 — 원문은 그 공백을 빼고 본다
        core = len(dateless[:at].rstrip())
        if end is None and bare is None and _RANGE_SEP.match(text[core:]):
            # "일" 없는 끝 날은 원문에서 "~" 가 날짜 바로 뒤일 때만 읽는다. "11/2 9:30~12" 의 12 는
            # 시각이다 — 시각을 지운 글에서는 "~" 가 날짜 바로 뒤처럼 보인다
            bare = _BARE_END_DAY.match(rest.strip())
        if bare:
            # "11월 9일~20일" — 뒤쪽은 달이 생략된다. 말일을 넘기면 다음 달로 읽지 않고 되묻는다
            end = _safe(start.year, start.month, int(bare["d"]))
            if end is None:
                reason = reason or f"없는 날짜예요: {bare[0].strip()}"
            elif _dow_mismatch(end, _written_weekday(bare)):
                reason = reason or f"요일이 달라요: {bare[0].strip()}"
        if end is None:
            open_ended = True
        else:
            split_at = at + sep.end()
    if end is not None and end < start:
        # 연말을 넘는 범위 — 12/29 ~ 1/2
        later = _safe(end.year + 1, end.month, end.day)
        if later and (later - start).days <= 92:
            end = later
        else:
            reason = reason or f"끝나는 날이 시작보다 빨라요: {text}"
            end, split_at = None, None

    # 범위로 이어지지 않은 날짜가 또 있으면 하나로 뭉개지 않는다 — 앞 날짜만 남기면 나머지가
    # 조용히 사라지고, 잇으면 없던 긴 행사가 생긴다
    rest_of_text = dateless[at:] if end is None else ""
    if reason is None and _first_date(rest_of_text, start, month)[0] is not None:
        reason = f"날짜가 여러 개예요: {text}"

    start_time, end_time, several, vague = _span_of_times(text, split_at)
    if several and reason is None:
        reason = f"시각이 여러 개예요: {text}"
    return NoticeWhen(
        start,
        start_time,
        end,
        end_time,
        reason,
        open_ended=open_ended,
        until=end is None and not open_ended and bool(_UNTIL.match(dateless[at:])),
        vague_time=vague,
    )


def _span_of_times(text: str, split_at: int | None) -> tuple[time | None, time | None, bool, bool]:
    """날짜 범위가 있으면 시각은 자기 쪽 날짜에 붙는다.

    "10월 27일 10:00 ~ 11월 17일 18:00" 의 18:00 은 끝 날의 시각이고, "12.27 ~ 1.10 15:00" 의
    15:00 은 시작 시각이 아니다. "11월 9일~20일 10:00~12:00" 은 매일의 시간이라 시작 · 끝이다.
    """
    if split_at is None:
        return _time_span(text)
    left_start, left_end, left_several, left_vague = _time_span(text[:split_at])
    right_start, right_end, right_several, right_vague = _time_span(text[split_at:])
    vague = left_vague or right_vague
    if left_start is None and right_end is not None:
        # 끝 날 쪽에만 시각이 있으면 매일의 시간이다. 거기에 범위가 또 있으면 그대로 되묻는다
        return right_start, right_end, right_several, vague
    return left_start, left_end or right_start, left_several or right_several, vague
