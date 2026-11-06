"""행사 일정 구절 읽기 — 원문 구절에서 날짜 · 요일 · 장소를 코드로 읽는다.

모델은 일정 원문에서 구절을 골라 오기만 한다(`event_schedule.py`). 그 구절이 어느 날인지는 여기서
읽는다 — 날짜 · 요일 계산은 100% 맞아야 해서 코드다(루트 §3). 모델이 요일 숫자 · 날짜를 직접 적던
때는 하나씩 밀려 적었다(11-03 실측).

🚨 못 읽는 구절은 읽었다고 하지 않는다(`Quoted.unreadable`) — 부르는 쪽이 그 행사를 모른다로
   닫는다. 격주 · 짝수 · 홀수 · 월을 알 수 없는 날 · 날짜와 안 맞는 (요일)이 그렇다.

순수 함수. LLM · DB · 외부 I/O 없음.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.agents.activity.events import Event

WEEKDAYS = "월화수목금토일"
_W = f"[{WEEKDAYS}]"
_ALL_DAYS = frozenset(range(7))

# ── 날짜 ──────────────────────────────────────────────────────────────────
_YMD = re.compile(r"(\d{4})\s*[.년/-]\s*(\d{1,2})\s*[.월/-]\s*(\d{1,2})\s*일?\.?")
_MD_KO = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_MD_DOT = re.compile(r"(?<![\d:])(\d{1,2})\s?[./]\s?(\d{1,2})(?!\s?[./]\s?\d)(?![\d:])\.?")
# 월 없는 "N일". "주 6일" · "3일간" 은 날짜가 아니다
_D_ONLY = re.compile(r"(?<![\d.:/])(?<!주\s)(?<!주)(\d{1,2})\s*일(?!\s*간)")
_PAREN_WD = re.compile(rf"^\s*\.?\s*\(\s*({_W})(?:요일)?\s*\)")
_RANGE_GAP = re.compile(r"^\s*(?:~|-|–|부터|에서)\s*$")

# ── 요일 ──────────────────────────────────────────────────────────────────
_NTH_WORDS = {
    "첫째": 1,
    "첫번째": 1,
    "둘째": 2,
    "두번째": 2,
    "셋째": 3,
    "세번째": 3,
    "넷째": 4,
    "네번째": 4,
    "다섯째": 5,
    "마지막": -1,
}
_NTH_ALT = "|".join(_NTH_WORDS)
_MONTHLY = re.compile(
    rf"(?:매월|매달)?\s*((?:{_NTH_ALT})\s*주?(?:\s*(?:,|·|/|및|와|과)\s*(?:{_NTH_ALT})\s*주?)*)"
    rf"\s*({_W})(?:요일)?(?:마다)?"
)
# X~Y · X요일~Y요일 · "목~일" (요일 글자 없이). 앞뒤가 숫자 · 한글이면 날짜("1일~3일")다
_WD_RANGE = re.compile(
    rf"(?<![\d가-힣])({_W})(?:요일)?\s*(?:~|-|–|부터)\s*({_W})(?:요일)?(?:\s*까지)?"
)
_WD_LIST = re.compile(
    rf"(?<!\d)({_W})(?:요일)?(?:\s*(?:,|·|/|및|와|과|\s)\s*({_W})(?:요일)?)+(?![가-힣])"
)
_WD_ONE = re.compile(rf"(?<!\d)({_W})요일|\(\s*({_W})\s*\)|\[\s*({_W})\s*\]")
# 시간 앞에 붙은 요일 — "토- 13:00" · 붙여 쓴 "22:00일- 11:00"
_WD_TIME = re.compile(
    rf"(?:(?<=\d:\d\d)|(?<![가-힣\d]))({_W})(?:요일)?\s*[-:：]\s*(?=\d{{1,2}}\s*[:시])"
)
_EVERY = re.compile(rf"매주\s*({_W})(?![가-힣])")
_UNREADABLE = re.compile(r"격주|짝수|홀수")

# ── 쉬는 날 ───────────────────────────────────────────────────────────────
CLOSED_MARK = re.compile(
    r"휴무|휴관|휴장|휴업|공연\s*없음|미운영|미진행|미개최|운영하지\s*않|진행하지\s*않|제외|쉽니다|쉼\b"
)
_WDS = rf"(?:{_W}요일\s*(?:[,·/및와과]\s*)?)+"
# "수요일 휴무" · "수요일은 공연 없음" · "매주 수요일 행사 미진행"
_CLOSED_BEFORE = re.compile(
    rf"({_WDS})\s*(?:은|는|에는)?\s*(?:(?:행사|공연|운영|전시|체험)\s*)?(?:정기\s*)?"
    r"(?:휴무|휴관|휴장|공연\s*없음|미운영|미진행)"
)
_CLOSED_AFTER = re.compile(
    rf"(?:휴무일|휴관일|정기\s*휴무|휴무|휴관|휴장)\s*[:：]?\s*(?:매주\s*)?({_WDS})"
)
# 쉬는 날 구절 안의 "(날짜 제외)" — 그날은 연다
_EXCEPT = re.compile(r"\(\s*([^()]{1,40}?)\s*(?:은|는)?\s*제외\s*\)")

# ── 요일 제한이 적힌 자리 ─────────────────────────────────────────────────
# 모델이 이 자리를 하나라도 빠뜨리면 모른다(`uncovered`). 두 번 읽을지도 이것으로 정한다
CUE = re.compile(
    rf"매주\s*{_W}(?:요일)?(?:\s*(?:,|·|/|~|부터)\s*{_W}(?:요일)?)*"
    rf"|(?<![\d요]){_W}(?:요일)?\s*(?:,|·|/)\s*{_W}(?:요일)?(?![가-힣])"
    rf"|{_W}요일\s*(?:에만|만|마다)"
    rf"|(?<!\d){_W}요일?\s*(?:~|부터)\s*{_W}요일"
    rf"|(?<![\d가-힣]){_W}\s*[~\-–]\s*{_W}(?:요일)?(?![가-힣])"
    r"|매월|매달|주말에만|평일에만|격주"
)
# 날짜가 적힌 자리. "3.2km" · "1.5배" 같은 수치는 날짜가 아니다
DATE_CUE = re.compile(
    rf"{_YMD.pattern}|{_MD_KO.pattern}|{_MD_DOT.pattern}(?!\s*(?:[a-zA-Z%]|배|㎞|㎡))"
)
# 원문만으로 날을 알 수 없다고 적은 것
UNDATED = re.compile(
    r"(?:일정|출연진|공연|날짜).{0,20}"
    r"(?:홈페이지|누리집|SNS|인스타그램|추후\s*공지|추후\s*공개|별도\s*공지)|회차별|회차마다"
)

# ── 운영 시간 칸 ──────────────────────────────────────────────────────────
# 운영 시간 칸에서 쉬는 날 표현을 찾는 문장 끝
_SENTENCE_END = re.compile(r"[.。※\n]|\s-\s")
_PT_EVERY = re.compile(rf"매주\s*((?:{_W}(?:요일)?\s*(?:[,·/및와과]|\s)\s*)*{_W}(?:요일)?)")
_PT_BRACKETED = re.compile(rf"\[\s*({_W})요일\s*\]")
_PT_LEADING = re.compile(rf"^\s*\(({_W})\)")


@dataclass
class Quoted:
    """구절 하나에서 읽은 날. 날짜와 요일을 같이 적었으면 둘 다 맞는 날이다."""

    dates: set[date] = field(default_factory=set)
    weekdays: set[int] = field(default_factory=set)
    monthly: list[tuple[int, int]] = field(default_factory=list)  # (몇째 주, 요일). 마지막 주 -1
    unreadable: bool = False
    outside: bool = False  # 날짜를 적었는데 전부 행사 기간 밖이다

    def has_weekly(self) -> bool:
        return bool(self.weekdays or self.monthly)

    def weekly(self, day: date) -> bool:
        return day.weekday() in self.weekdays or any(
            weekday == day.weekday() and nth(day, n) for n, weekday in self.monthly
        )

    def days(self, window: Iterable[date]) -> set[date] | None:
        """창 안에서 이 구절이 말하는 날. 날짜도 요일도 없으면 None."""
        if self.dates:
            return {
                d for d in window if d in self.dates and (not self.has_weekly() or self.weekly(d))
            }
        if self.has_weekly():
            return {d for d in window if self.weekly(d)}
        return None

    def any_days(self, window: Iterable[date]) -> set[date] | None:
        """창 안에서 날짜이거나 요일인 날. 쉬는 날 구절은 둘 중 하나만 맞아도 쉰다("매주 화요일과
        9월 25일은 휴무"). 날짜도 요일도 없으면 None."""
        if not (self.dates or self.has_weekly()):
            return None
        return {d for d in window if d in self.dates or self.weekly(d)}


def parse(quote: str, event: Event) -> Quoted:
    """구절의 날짜 · 요일. 연도는 행사 기간에서 정한다."""
    quoted = Quoted()
    text = " ".join(quote.split())
    if _UNREADABLE.search(text):
        quoted.unreadable = True
        return quoted
    tokens = _date_tokens(text, event)
    if tokens is None:
        quoted.unreadable = True
        return quoted
    resolved: list[tuple[int, int, date | None]] = []
    for start, end, (month, day_of, year) in tokens:
        day = _in_period(event, month, day_of, year)
        # 날짜 바로 뒤 (요일)은 그 날짜와 맞아야 한다 — 원문 오타면 어느 쪽인지 모른다
        if found := _PAREN_WD.match(text[end:]):
            if day and WEEKDAYS.index(found[1]) != day.weekday():
                quoted.unreadable = True
                return quoted
            end += found.end()
        resolved.append((start, end, day))
    if resolved and not any(day for _, _, day in resolved):
        quoted.outside = True
        return quoted
    used = _read_dates(text, resolved, event, quoted.dates)
    rest = _blank(text, used)
    rest = _read_weekdays(rest, quoted)
    if "평일" in rest:
        quoted.weekdays |= {0, 1, 2, 3, 4}
    if "주말" in rest:
        quoted.weekdays |= {5, 6}
    if "매일" in rest:
        quoted.weekdays |= set(_ALL_DAYS)
    return quoted


def _date_tokens(
    text: str, event: Event
) -> list[tuple[int, int, tuple[int, int, int | None]]] | None:
    """날짜 토큰 (시작, 끝, (월, 일, 연도)) 을 왼쪽부터. 월을 알 수 없는 "N일" 이 있으면 None."""
    taken = [False] * len(text)
    tokens: list[tuple[int, int, tuple[int, int, int | None]]] = []

    def take(found: re.Match[str], month: int, day_of: int, year: int | None = None) -> None:
        if any(taken[found.start() : found.end()]):
            return
        for k in range(found.start(), found.end()):
            taken[k] = True
        tokens.append((found.start(), found.end(), (month, day_of, year)))
        tokens.sort()

    for found in _YMD.finditer(text):
        take(found, int(found[2]), int(found[3]), int(found[1]))
    for found in _MD_KO.finditer(text):
        take(found, int(found[1]), int(found[2]))
    for found in _MD_DOT.finditer(text):
        take(found, int(found[1]), int(found[2]))
    for found in _D_ONLY.finditer(text):
        if any(taken[found.start() : found.end()]):
            continue
        day_of = int(found[1])
        before = [token for token in tokens if token[1] <= found.start()]
        if before:
            # 앞 날짜의 월을 잇는다. "10월 23일, 24일, 6일" 의 6일은 다음 달이다
            month, prev_day, year = before[-1][2]
            if day_of < prev_day:
                month, year = month % 12 + 1, None
            take(found, month, day_of, year)
            continue
        # 앞 날짜가 없으면 기간 안에 그날이 하나뿐일 때만 그 달이다("10일부터 31일까지 매주 토요일")
        candidates = [day for day in _period_days(event) if day.day == day_of]
        if len(candidates) != 1:
            return None
        take(found, candidates[0].month, day_of, candidates[0].year)
    return tokens


def _read_dates(
    text: str, resolved: list[tuple[int, int, date | None]], event: Event, out: set[date]
) -> list[tuple[int, int]]:
    """날짜 · 날짜 범위 · 한쪽 끝("~까지" · "~부터")을 `out` 에 넣고, 읽은 자리를 돌려준다."""
    used: list[tuple[int, int]] = []
    k = 0
    while k < len(resolved):
        start, end, day = resolved[k]
        if k + 1 < len(resolved) and _RANGE_GAP.match(text[end : resolved[k + 1][0]]):
            _, end2, day2 = resolved[k + 1]
            if text[end2:].lstrip().startswith("까지"):
                end2 = end2 + text[end2:].index("까지") + 2
            if day or day2:
                out |= _between(event, day or event.starts_on, day2 or event.ends_on)
            used.append((start, end2))
            k += 2
            continue
        if day:
            tail = text[end:].lstrip()
            if tail.startswith("까지"):
                out |= _between(event, event.starts_on, day)
            elif tail.startswith("부터"):
                out |= _between(event, day, event.ends_on)
            else:
                out.add(day)
        used.append((start, end))
        k += 1
    return used


def _read_weekdays(rest: str, quoted: Quoted) -> str:
    """요일 표기를 읽어 `quoted` 에 넣고, 읽은 자리를 지운 글을 돌려준다."""
    for found in list(_MONTHLY.finditer(rest)):
        for word in re.findall(_NTH_ALT, found[1].replace(" ", "")):
            quoted.monthly.append((_NTH_WORDS[word], WEEKDAYS.index(found[2])))
        rest = _blank(rest, [(found.start(), found.end())])
    for found in list(_WD_RANGE.finditer(rest)):
        quoted.weekdays |= span(found[1], found[2])
        rest = _blank(rest, [(found.start(), found.end())])
    for found in list(_WD_LIST.finditer(rest)):
        quoted.weekdays |= {WEEKDAYS.index(c) for c in re.findall(_W, found[0].replace("요일", ""))}
        rest = _blank(rest, [(found.start(), found.end())])
    for pattern in (_WD_TIME, _WD_ONE, _EVERY):
        for found in list(pattern.finditer(rest)):
            quoted.weekdays.add(WEEKDAYS.index(next(g for g in found.groups() if g)))
            rest = _blank(rest, [(found.start(), found.end())])
    return rest


def closed_weekdays(text: str) -> set[int]:
    """원문 어디든 "○요일 휴무" · "휴무일: ○요일" 의 요일 — 모델이 구절을 빠뜨려도 뺀다."""
    out: set[int] = set()
    for pattern in (_CLOSED_BEFORE, _CLOSED_AFTER):
        for found in pattern.finditer(text):
            out |= {WEEKDAYS.index(day) for day in re.findall(rf"({_W})요일", found[1])}
    return out


def without_exceptions(quote: str) -> str:
    """쉬는 날 구절에서 "(날짜 제외)" 를 뗀다 — 남은 것이 쉬는 날이다."""
    return _EXCEPT.sub(" ", quote)


def reopened(text: str, event: Event) -> set[date]:
    """쉬는 날 표현 바로 뒤 "(날짜 제외)" 의 날짜 — 쉬는 요일이어도 그날은 연다."""
    out: set[date] = set()
    for found in _EXCEPT.finditer(text):
        if not CLOSED_MARK.search(text[max(0, found.start() - 40) : found.start()]):
            continue
        quoted = parse(found[1], event)
        if not quoted.unreadable and not quoted.has_weekly():
            out |= quoted.dates
    return out


def weekday_cues(text: str) -> list[str]:
    """요일 제한이 적힌 자리."""
    return [found[0] for found in CUE.finditer(text)]


def cue_spots(text: str) -> list[re.Match[str]]:
    """요일 제한 · 날짜가 적힌 자리, 나온 순서대로. 모델이 하나라도 빠뜨리면 모른다."""
    found = [*CUE.finditer(text), *DATE_CUE.finditer(text)]
    return sorted(found, key=lambda match: match.start())


def uncovered(text: str, quotes: Sequence[str]) -> list[str]:
    """요일 제한 · 날짜가 적힌 자리 가운데 어느 구절에도 들지 않은 것 — 모델이 빠뜨렸다."""
    norm, where = _norm_map(text)
    spans: list[tuple[int, int]] = []
    for quote in quotes:
        key = norm_text(quote)
        if not key:
            continue
        k = norm.find(key)
        while k >= 0:
            spans.append((where[k], where[k + len(key) - 1]))
            k = norm.find(key, k + 1)
    return [
        found[0]
        for found in cue_spots(text)
        if not any(a <= found.start() and found.end() - 1 <= b + 2 for a, b in spans)
    ]


def playtime_weekdays(playtime: str) -> set[int]:
    """운영 시간 칸이 여는 요일로 적은 것 — 매주 X · X~Y · [X요일] · 맨 앞 (X). 없으면 빈 집합.

    시간만 다른 요일("토요일은 21:00까지") · 쉬는 요일은 여는 요일이 아니다. 일곱 요일이 다
    나오면 제한이 아니다.
    """
    text = " ".join(playtime.split())
    found_days: set[int] = set()
    for pattern in (_PT_EVERY, _WD_RANGE, _PT_BRACKETED, _PT_LEADING):
        for found in pattern.finditer(text):
            # 쉬는 날 표현은 같은 문장 뒤쪽에 올 수 있다("매주 수요일 및 10/14~10/22 행사 미진행")
            stop = _SENTENCE_END.search(text, found.end())
            end = min(stop.start() if stop else len(text), found.end() + 30)
            near = text[max(0, found.start() - 10) : end]
            if CLOSED_MARK.search(near):
                continue
            if pattern is _WD_RANGE:
                found_days |= span(found[1], found[2])
            else:
                # "토요일" 의 "일" 은 일요일이 아니다 — "요일" 을 떼고 읽는다
                bare = found[1].replace("요일", "")
                found_days |= {WEEKDAYS.index(day) for day in re.findall(_W, bare)}
    return set() if found_days == _ALL_DAYS else found_days


# ── 장소 ──────────────────────────────────────────────────────────────────


def segments(place: str) -> list[str]:
    """소개정보 장소 칸의 칸들 — "갑 공원 / 을 광장" 은 두 칸이다."""
    parts = re.split(r"\s*(?:/|,|·|및|\||\n)\s*", place)
    return [part.strip() for part in parts if len(norm_text(part)) >= 2]


def venue_of(quote: str, place: str) -> str | None:
    """구절에 든 장소 칸. 정확히 한 칸이어야 한다 — 원문 아무 낱말을 장소로 옮기지 않는다."""
    key = norm_text(quote)
    hits = [seg for seg, names in _venue_keys(place) if any(name in key for name in names)]
    return hits[0] if len(hits) == 1 else None


def venue_pairs(quotes: Sequence[str], text: str, place: str) -> dict[str, str]:
    """날짜 구절마다 장소 칸 — 장소가 날짜 바로 뒤(다음 날짜 전까지)에 오는 목록인지, 바로
    앞(앞 날짜 뒤부터)에 오는 목록인지 원문 배치로 정한다.

    한쪽으로 읽을 때 모든 날짜에 장소가 정해져야 그쪽이다. 장소가 안 보이는 날짜는 같은 쪽 이웃의
    장소를 잇는다("물빛공원 11월 7일, 11월 14일"). 양쪽 다 되거나 둘 다 안 되면 짝짓지 않는다 —
    다른 날 장소를 붙이느니 장소 없이 낸다.
    """
    keys = _venue_keys(place)
    if len(keys) < 2 or not quotes:
        return {}
    norm = norm_text(text)
    spots: list[tuple[int, int, str]] = []
    for quote in dict.fromkeys(quotes):
        key = norm_text(quote)
        k = norm.find(key) if key else -1
        if k < 0:
            return {}
        spots.append((k, k + len(key), quote))
    spots.sort()

    def found(a: int, b: int) -> set[str]:
        piece = norm[max(0, a) : max(0, b)]
        return {seg for seg, names in keys if any(name in piece for name in names)}

    after = [
        found(end, spots[k + 1][0] if k + 1 < len(spots) else end + 40)
        for k, (_, end, _) in enumerate(spots)
    ]
    before = [
        found(spots[k - 1][1] if k else start - 40, start) for k, (start, _, _) in enumerate(spots)
    ]

    def carry(sets: list[set[str]], order: Iterable[int]) -> dict[str, str] | None:
        out: dict[str, str] = {}
        current: str | None = None
        for k in order:
            if len(sets[k]) > 1:
                return None
            if sets[k]:
                current = next(iter(sets[k]))
            if current is None:
                return None
            out[spots[k][2]] = current
        return out

    by_before = carry(before, range(len(spots)))
    by_after = carry(after, reversed(range(len(spots))))
    if (by_before is None) == (by_after is None):
        return {}
    return by_before or by_after or {}


def _venue_keys(place: str) -> list[tuple[str, set[str]]]:
    """장소 칸마다 찾을 이름 — 칸 전체와 3자 이상인 첫 낱말("물빛공원 잔디광장" → "물빛공원")."""
    out = []
    for seg in segments(place):
        words = seg.split()
        names = {norm_text(seg), norm_text(words[0]) if words else ""}
        out.append((seg, {name for name in names if len(name) >= 3}))
    return out


# ── 공용 ──────────────────────────────────────────────────────────────────


def span(first: str, last: str) -> set[int]:
    """X요일부터 Y요일까지의 요일. 주를 넘어가도 된다(토~월)."""
    start, end = WEEKDAYS.index(first), WEEKDAYS.index(last)
    return {(start + offset) % 7 for offset in range((end - start) % 7 + 1)}


def nth(day: date, n: int) -> bool:
    """그날이 그 달의 n째 주 같은 요일인가. -1 은 마지막 주다."""
    if n == -1:
        return (day + timedelta(days=7)).month != day.month
    return (day.day - 1) // 7 + 1 == n


def norm_text(text: str) -> str:
    """띄어쓰기 · 문장부호를 뗀 글 — 구절을 원문과 대조할 때 쓴다."""
    return re.sub(r"[\s\W_]+", "", text).casefold()


def _norm_map(text: str) -> tuple[str, list[int]]:
    """정규화한 글과, 그 글자마다 원문 위치."""
    chars: list[str] = []
    where: list[int] = []
    for k, char in enumerate(text):
        if kept := norm_text(char):
            chars.append(kept)
            where.append(k)
    return "".join(chars), where


def _blank(text: str, used: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in used:
        for k in range(start, min(end, len(chars))):
            chars[k] = " "
    return "".join(chars)


def _period_days(event: Event) -> list[date]:
    return [
        event.starts_on + timedelta(days=k)
        for k in range((event.ends_on - event.starts_on).days + 1)
    ]


def _between(event: Event, low: date, high: date) -> set[date]:
    return {day for day in _period_days(event) if low <= day <= high}


def _in_period(event: Event, month: int, day_of: int, year: int | None) -> date | None:
    """그 월 · 일이 행사 기간 안이면 그날. 연도를 안 적었으면 기간의 연도에서 찾는다."""
    if not (1 <= month <= 12 and 1 <= day_of <= 31):
        return None
    for candidate_year in [year] if year else [event.starts_on.year, event.ends_on.year]:
        try:
            day = date(candidate_year, month, day_of)
        except ValueError:
            return None
        if event.starts_on <= day <= event.ends_on:
            return day
    return None
