"""복약 시점 사전 · 기간 — `resolve_dose_timing` · `resolve_period`.

보호자는 "식후", "자기 전" 처럼 **시점**으로 말한다. 모델은 그 표현을 복사만 하고(span), 어떤
기준점 + 오프셋인지는 이 사전이 정한다. 모델이 `fixed` 를 고를 수 없다 — 발화에 시각이 있을 때만
`fixed` 다.

- 사전에 없거나 시점이 빠진 말은 **풀지 않는다**(None). "하루 세 번" 만으로 식후를 짐작하면 틀린
  알림이 나간다. 호출하는 쪽이 되묻는다. 오전 · 오후를 모르는 "3시" 도 짐작하지 않는다.
- 끼니가 붙으면 그 끼니, 안 붙고 "하루 세 번" 이면 세 끼니, "하루 두 번" 이면 아침 · 저녁이다.
  끼니 수와 말한 횟수가 안 맞으면(하루 세 번 + 오후 3시) 풀지 않는다.
- offset 은 DB CHECK(`OFFSET_MIN` ~ `OFFSET_MAX`) 안이다. 밖이면 자르지 않고 풀지 않는다.
- 영아(`INFANT_BEDTIME_BELOW_MONTH` 미만)는 "자기 전" 을 취침 기준 시각 대신 고정 19:30 으로
  낸다 (M-3).
- 기간은 오늘 포함이다("5일" = 오늘 + 4일). 기간이 없으면 `ends_on` 을 짐작하지 않는다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

from app.agents.common.reference import age_gates
from app.agents.health.tools.spans import normalize_spaces
from app.core.medication_draft import OFFSET_MAX, OFFSET_MIN, TimingAnchor, scheduled_time_of

# 영아(0–23개월)는 자기 전을 취침 상수 대신 fixed 19:30 으로 낸다. 경계는 age_gates.yaml 이 정본이다
INFANT_BEDTIME_BELOW_MONTH = age_gates().infant_bedtime_below_month
INFANT_BEDTIME = time(19, 30)

DEFAULT_AFTER_MEAL = 30  # "식후" · "밥 먹고"
DEFAULT_BEFORE_MEAL = -30  # "식전" · "밥 먹기 전"

# 말한 횟수 → 끼니. 셋이면 세 끼니, 둘이면 아침 · 저녁. 그 밖의 횟수는 끼니로 나눌 수 없다
_MEALS_BY_COUNT: dict[int, tuple[TimingAnchor, ...]] = {
    2: ("breakfast", "dinner"),
    3: ("breakfast", "lunch", "dinner"),
}
_MEAL_WORDS: dict[str, TimingAnchor] = {"아침": "breakfast", "점심": "lunch", "저녁": "dinner"}
_NUMBER_WORDS = {"한": 1, "두": 2, "세": 3, "네": 4}

_BEDTIME = re.compile(r"(?:잠자기|자기|취침)\s*전|잠들기\s*전")
_WAKE = re.compile(r"일어나(?:서|자마자)|기상\s*(?:후|직후)")
_MARKERS = "오전|오후|아침|낮|점심|저녁|밤|새벽"
# 표지 바로 뒤의 조사("저녁은 7시")까지는 붙은 것으로 본다
_CLOCK = re.compile(
    rf"(?:(?P<marker>{_MARKERS})(?:에는|은|는|에|만|도)?\s*)?(?P<hour>\d{{1,2}})\s*시(?!간)"
    r"(?:\s*(?:(?P<minute>\d{1,2})\s*분|(?P<half>반)))?"
)
# "일곱 시" 같은 한글 숫자 시각은 읽지 않는다. 읽지 못한 것을 버리고 끼니만 남기면 틀린 시각이
# 된다. "한 시기" · "두 시간" 은 시각이 아니라서, 뒤가 시각의 끝(조사 · 문장부호 · 끝)일 때만 센다
_HANGUL_CLOCK = re.compile(
    r"(?:열두|열한|다섯|여섯|일곱|여덟|아홉|열|한|두|세|네)\s*시"
    r"(?=$|[\s,.!?]|에|로|쯤|경|까지|부터|마다|반|정각)"
)
_COUNT = re.compile(r"(?:(?:하루|1일)\s*)?(\d+|한|두|세|네)\s*(?:번|회)(?!에)")
# 시점의 크기. 앞의 둘이 숫자가 붙은 표현이라 먼저 본다
_OFFSET_RELATIVE = re.compile(r"(\d+)\s*(분|시간)\s*(전|후|뒤)")
_OFFSET_AFTER_MEAL = re.compile(r"(?:식후|식사\s*후|먹고)\s*(\d+)\s*(분|시간)")
_OFFSET_BEFORE_MEAL = re.compile(r"(?:식전|식사\s*전)\s*(\d+)\s*(분|시간)")
_IMMEDIATE = re.compile(r"(?:식후|식사\s*후|먹고)\s*(?:바로|곧바로|직후)|식사\s*직후|먹자마자")
_BEFORE = re.compile(r"식전|식사\s*전|먹기\s*전")
_AFTER = re.compile(r"식후|식사\s*후|(?:밥|아침|점심|저녁)\s*먹고|먹고\s*나서|먹은\s*(?:뒤|후)")
_MEAL = re.compile("|".join(_MEAL_WORDS))


@dataclass(frozen=True)
class ResolvedDose:
    timing_anchor: TimingAnchor
    offset_minutes: int
    fixed_time: time | None

    @property
    def scheduled_time(self) -> time:
        return scheduled_time_of(self.timing_anchor, self.offset_minutes, self.fixed_time)


@dataclass
class _Parsed:
    """span 하나에서 읽은 것."""

    clocks: list[time]
    bedtime: bool = False
    wake: bool = False
    count: int | None = None
    offset: int | None = None  # 식전 · 식후 · N분 전후. 말하지 않았으면 None
    meals: tuple[TimingAnchor, ...] = ()
    ambiguous: bool = False  # 오전 · 오후를 모르는 시각


def resolve_dose_timing(spans: Sequence[str], *, months: int) -> tuple[ResolvedDose, ...] | None:
    """시점 표현들 → 하루 시각 패턴. 풀지 못하면 None — 호출하는 쪽이 되묻는다.

    `spans` 는 모델이 복사한 표현이거나, 되묻기에 대한 답까지 이어 붙인 것이다
    (`["하루 세 번", "식후요"]`). 시각순으로 정렬하고 같은 시각은 하나로 합친다.
    """
    parsed = [_parse(normalize_spaces(span)) for span in spans if span.strip()]
    if not parsed or any(p.ambiguous for p in parsed):
        return None
    count = next((p.count for p in parsed if p.count is not None), None)
    # 끼니 없이 시점만 말한 span("식후요")의 값은 끼니만 말한 span("아침") 에도 쓴다
    shared = next((p.offset for p in parsed if p.offset is not None and not p.meals), None)

    doses: list[ResolvedDose] = []
    for p in parsed:
        meal_offset = p.offset if p.offset is not None else (shared or 0)
        doses += [ResolvedDose(meal, meal_offset, None) for meal in p.meals]
        doses += [ResolvedDose("fixed", 0, clock) for clock in p.clocks]
        if p.wake:
            doses.append(ResolvedDose("wake_up", 0, None))
        if p.bedtime:
            doses.append(_bedtime(months))

    if doses:
        if count is not None and len(set(doses)) != count:
            return None  # "하루 세 번" 인데 시각은 하나 — 어느 쪽이 맞는지 모른다
    else:
        # 끼니도 시각도 없다 — "하루 세 번 식후" 만 풀린다. 시점(식전 · 식후)이 없으면 풀지 않는다
        offset = next((p.offset for p in parsed if p.offset is not None), None)
        if offset is None or count not in _MEALS_BY_COUNT:
            return None
        doses = [ResolvedDose(meal, offset, None) for meal in _MEALS_BY_COUNT[count]]

    if any(not OFFSET_MIN <= dose.offset_minutes <= OFFSET_MAX for dose in doses):
        return None
    return tuple(sorted(set(doses), key=lambda dose: dose.scheduled_time))


def anchors_named(spans: Sequence[str]) -> set[TimingAnchor]:
    """말에 들어 있는 끼니 · 자기 전 · 일어나서. 수정이 어느 회차를 바꾸는지 가리는 데 쓴다.

    "저녁 시간 7시로" 는 저녁 회차만 바꾼다 — 아침 · 점심 회차가 같이 사라지면 안 된다.
    """
    named: set[TimingAnchor] = set()
    for span in spans:
        text = normalize_spaces(span)
        if _BEDTIME.search(text):
            named.add("bedtime")
        if _WAKE.search(text):
            named.add("wake_up")
        text = _WAKE.sub(" ", _BEDTIME.sub(" ", text))
        named.update(_MEAL_WORDS[word] for word in _MEAL.findall(text))
    return named


def unclear_clock(spans: Sequence[str]) -> bool:
    """시각을 읽다 모호해서 못 풀었나 — 오전 · 오후를 모르는 "8시에", 한글 숫자 "일곱 시" 같은 것.

    이때 되묻는 말은 식전 · 식후가 아니라 몇 시인지여야 한다.
    """
    return any(_parse(normalize_spaces(span)).ambiguous for span in spans if span.strip())


def resolve_dose_answer(
    spans: Sequence[str], answer: str, *, months: int
) -> tuple[ResolvedDose, ...] | None:
    """되묻기에 대한 답으로 시점을 푼다. 원래 말한 표현에 답을 이어 붙이는 것이 기본이다.

    원래 표현이 모호한 시각("8시에")이면 그대로 이어 붙여서는 계속 모호하다. 답이 오전 · 오후를
    말했으면("저녁이요") 한 말로 합쳐 읽고, 답이 시각까지 다시 말했으면("저녁 8시요") 답을 쓴다.
    """
    direct = resolve_dose_timing([*spans, answer], months=months)
    if direct is not None or not unclear_clock(spans):
        return direct
    joined = resolve_dose_timing([" ".join([*spans, answer])], months=months)
    if joined is not None:
        return joined
    return resolve_dose_timing([answer], months=months) if _CLOCK.search(answer) else None


def _bedtime(months: int) -> ResolvedDose:
    if months < INFANT_BEDTIME_BELOW_MONTH:
        return ResolvedDose("fixed", 0, INFANT_BEDTIME)
    return ResolvedDose("bedtime", 0, None)


def _parse(text: str) -> _Parsed:
    parsed = _Parsed(clocks=[])

    parsed.bedtime = bool(_BEDTIME.search(text))
    text = _BEDTIME.sub(" ", text)
    parsed.wake = bool(_WAKE.search(text))
    text = _WAKE.sub(" ", text)

    if _HANGUL_CLOCK.search(text):
        parsed.ambiguous = True
    had_direction = bool(_offset(text)[0] is not None or "먹고" in text)

    # 시각. 바로 앞에 오전 · 오후가 없으면 같은 span 의 다른 자리("저녁 시간 7시")에서 찾되, span 에
    # 표지도 시각도 **하나뿐일 때만** 이어 붙인다 — 둘 이상이면 어느 표지가 어느 시각 것인지 모른다
    clocks = list(_CLOCK.finditer(text))
    markers = re.findall(_MARKERS, text)
    fallback = re.search(_MARKERS, text) if len(markers) == 1 and len(clocks) == 1 else None
    used_fallback = False
    for match in clocks:
        marker = match["marker"]
        if marker is None and fallback is not None:
            marker, used_fallback = fallback.group(), True
        clock = _clock_time(marker, int(match["hour"]), match)
        if clock is None:
            parsed.ambiguous = True
        else:
            parsed.clocks.append(clock)
    text = _CLOCK.sub(" ", text)
    if used_fallback and fallback is not None:
        text = text.replace(fallback.group(), " ", 1)

    if count := _COUNT.search(text):
        word = count.group(1)
        parsed.count = int(word) if word.isdigit() else _NUMBER_WORDS[word]
        text = _COUNT.sub(" ", text)

    parsed.offset, text = _offset(text)
    parsed.meals = tuple(dict.fromkeys(_MEAL_WORDS[word] for word in _MEAL.findall(text)))
    if parsed.clocks and (parsed.meals or had_direction):
        # 시각과 끼니 · 식전 식후가 한 말에 섞였다("점심은 빼고 저녁만 7시로", "아침 먹고, 7시") —
        # 남은 끼니를 새 회차로 읽으면 빼라는 것이 들어가고 식후 회차가 사라진다
        parsed.ambiguous = True
    return parsed


def _clock_time(marker: str | None, hour: int, match: re.Match[str]) -> time | None:
    """ "N시" 를 24시간 시각으로. 오전 · 오후를 알 수 없는 시각은 짐작하지 않는다."""
    if marker == "밤":
        # 밤 1~4시는 자정 뒤(새벽), 6~11시는 저녁이다. 5시는 어느 쪽인지 모른다
        if hour == 12 or hour <= 4:
            hour = hour % 12
        elif hour == 5:
            return None
        elif hour < 12:
            hour += 12
    elif marker in ("오후", "저녁"):
        hour = hour if hour >= 12 else hour + 12
    elif marker in ("낮", "점심"):
        hour = hour + 12 if hour < 6 else hour
    elif marker in ("오전", "아침", "새벽"):
        hour = 0 if hour == 12 else hour
    elif not (hour >= 13 or hour == 0):
        return None
    minute = 30 if match["half"] else int(match["minute"] or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _offset(text: str) -> tuple[int | None, str]:
    """식전 · 식후 · N분 전후 → offset(분). 말하지 않았으면 None. 읽은 부분은 text 에서 뺀다."""
    if match := _OFFSET_RELATIVE.search(text):
        sign = -1 if match.group(3) == "전" else 1
        return sign * _minutes(match), _OFFSET_RELATIVE.sub(" ", text)
    if match := _OFFSET_AFTER_MEAL.search(text):
        return _minutes(match), _OFFSET_AFTER_MEAL.sub(" ", text)
    if match := _OFFSET_BEFORE_MEAL.search(text):
        return -_minutes(match), _OFFSET_BEFORE_MEAL.sub(" ", text)
    if _IMMEDIATE.search(text):
        return 0, text
    if _BEFORE.search(text):
        return DEFAULT_BEFORE_MEAL, text
    if _AFTER.search(text):
        return DEFAULT_AFTER_MEAL, text
    return None, text


def _minutes(match: re.Match[str]) -> int:
    number, unit = int(match.group(1)), match.group(2)
    return number * 60 if unit == "시간" else number


_DAY_WORDS = {"이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5, "엿새": 6, "이레": 7, "열흘": 10}
_WEEK = re.compile(r"(\d+)\s*주")
_DAY = re.compile(r"(\d+)\s*일")
# 달력 날짜 · 요일 표현은 기간이 아니다 — "10월 25일까지" 를 25일로 읽으면 알림이 몇 주 더 간다
_CALENDAR = re.compile(r"\d+\s*월|[다이]음\s*[달주]|이번\s*[달주]|요일|내일|모레")
# "5일째" · "25일까지" · "25일부터" 는 날짜(순번)이고, "5일간" · "5일치" · "5일 먹이래" 는 기간이다
_NOT_DURATION_AFTER = ("까지", "째", "부터", "에서", "에", "마다")


def resolve_period(span: str | None, *, today: date) -> tuple[date, date | None] | None:
    """기간 표현 → (starts_on, ends_on). 오늘 포함이다 — "5일" 은 오늘부터 +4일.

    기간이 없으면 `(today, None)` 이다 — `ends_on` 을 짐작하지 않고 초안의 `missing` 으로 넘긴다.
    읽을 수 없는 말("다음 주 월요일까지")은 None 이다.
    """
    if span is None or not span.strip():
        return today, None
    text = normalize_spaces(span)
    if _CALENDAR.search(text):
        return None
    days: int | None = None
    if "일주일" in text:
        days = 7
    elif word := next((w for w in _DAY_WORDS if w in text), None):
        days = _DAY_WORDS[word]
    elif match := _WEEK.search(text):
        days = int(match.group(1)) * 7
    elif found := list(_DAY.finditer(text)):
        # 날이 둘이면("3일에서 5일") 기간이 아니다. 날 뒤가 날짜 표현이어도 아니다
        if len(found) > 1 or text[found[0].end() :].lstrip().startswith(_NOT_DURATION_AFTER):
            return None
        days = int(found[0].group(1))
    if days is None or days < 1:
        return None
    return today, today + timedelta(days=days - 1)
