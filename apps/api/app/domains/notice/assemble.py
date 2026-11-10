"""모델이 읽은 공지 → 저장할 행 · 일정 초안. 모델을 부르지 않는 규칙 코드다.

모델이 낸 값을 믿지 않고 여기서 검사한다. 날짜가 어긋나거나, 근거 문장이 원문에 없거나, 금액 성격이
빠지면 값을 지어내지 않고 `needs_review` 로 표시한다 — 보호자가 확인 화면에서 고친다.
"""

import hashlib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, tzinfo
from difflib import SequenceMatcher

from app.core.event_draft import CreateEventDraft, DraftItemPayload, EventBody
from app.core.notice_extract import ExtractedItem, ItemDetail, NoticeExtract
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItemHandling,
    NoticeItemTarget,
)
from app.domains.suggestion.draft_items import draft_items
from app.rules.notice_dates import NoticeWhen, _times, parse_base_month, parse_notice_when
from app.rules.notice_text import clean_text

MAX_EVENT_TITLE = 200
QUOTE_OVERLAP = 0.7
SAME_NOTICE_RATIO = 0.97
# details 는 화면에 한 줄씩 그린다. 잘린 뒷부분은 raw_text 에 남는다
MAX_DETAILS = 12
MAX_DETAIL_NAME = 30
MAX_DETAIL_VALUE = 200
_PHONE = re.compile(r"\b0\d{1,2}[-.\s)]{0,2}\d{3,4}[-.\s]?\d{4}\b")
_SPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")
_TIME_NAME = re.compile(r"^(?:행사\s*|교육\s*|진행\s*)?(?:시간|시각|일시)$")
_TIME_IN_VALUE = re.compile(r"\d{1,2}\s*(?::\s*\d{2}|시)")
_DATE_IN_VALUE = re.compile(r"\d{1,2}\s*(?:월\s*\d{1,2}\s*일|[/.]\s*\d{1,2})")
# 가리는 기관은 아이가 다니는 곳뿐이다. 반 · 행사 장소까지 가리면 "어느 반이 얼마" 가 사라진다
_OWN_ORG = re.compile(r".*?(?:어린이집|유치원)")
# 고유 이름이 아니다. 가리면 "어린이집" 같은 일반 문장이 "[기관]" 이 된다
_GENERIC_ORGS = frozenset({"어린이집", "유치원"})
# 짧은 이름은 낱말 안에 잘 들어 있다 — "하나씩" · "보라색". 뒤에 호칭 · 조사가 올 때만 이름으로 본다
_ORG_SUFFIX = re.compile(r"\s*(?:어린이집|유치원)$")
_NAME_TAIL = (
    r"(?=$|[^가-힣]|이|가|은|는|을|를|의|에|와|과|도|로|랑|아|야|씨|님|군|양"
    r"|어린이|어머|아버|선생|친구)"
)
_SHORT_NAME = 2
# 이보다 짧은 기관 앞부분은 "새싹" 처럼 낱말일 수 있어 약칭으로 치지 않는다
_MIN_ORG_STEM = 3
_OPENS = re.compile(r"(?:신청|접수|예약|모집)\s*시작|시작\s*일시|시작\s*시각|오픈")
_QUOTE_NOISE = re.compile(r"[\s|│·ㆍ:：\-–—*•◆■□○●▶]+")
_END_OF_DAY = time(23, 59)
# 모델이 값을 고르며 한 생각이 제목 · details 에 섞인다 — "… Notice 포함 Info 등 겸함이나" (#909).
# 공지에 없는 라틴 문자 낱말이 둘 이상이면 그 표시로 본다. 혼잣말은 영어 구절로 나온다 —
# 낱말 하나는 원문의 "줌" 을 "Zoom" 으로 적은 정당한 값일 수 있다.
# 두 글자 이하는 단위 · 약어라 세지 않는다
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
_LATIN_RUN = re.compile(r"[A-Za-z]+")
_MONOLOGUE_WORDS = 2
_OPEN_PAREN = re.compile(r"[(（]")
# 규칙이 날짜 · 시각으로 쓰지 못해 화면에서 사라질 글을 이 이름표로 details 에 남긴다
WRITTEN_AS = "적힌 대로"
_MARKS = re.compile(r"[\W_]+")
_UNTIL_AFTER = re.compile(r"\s*까지")


@dataclass(frozen=True)
class NewNoticeItem:
    handling: NoticeItemHandling
    label: str
    title: str
    target_scope: NoticeItemTarget
    starts_at: datetime | None
    ends_at: datetime | None
    all_day: bool
    due_at: datetime | None
    due_all_day: bool
    details: tuple[ItemDetail, ...]
    supplies: tuple[str, ...]
    source_quote: str
    # 확인 화면에서만 쓴다. 확인하지 않은 항목은 저장되지 않아서 표에는 칸이 없다
    needs_review: bool
    review_reason: str | None


@dataclass(frozen=True)
class NewNotice:
    doc_kind: NoticeDocKind
    input_format: NoticeInputFormat
    audience_scope: NoticeAudience
    issued_on: date | None
    received_at: datetime
    title: str | None
    raw_text: str
    content_hash: str
    is_correction: bool
    items: tuple[NewNoticeItem, ...]
    extractor_version: str | None = None


def _squeeze(text: str) -> str:
    return _SPACE.sub("", text)


def _quote_key(text: str) -> str:
    """근거 대조용. 표를 옮겨 적을 때 끼는 구분 기호는 글자가 아니다."""
    return _QUOTE_NOISE.sub("", text)


def _quoted(quote: str, source: str) -> bool:
    """모델은 떨어진 줄을 이어 붙여 인용하기도 한다. 줄마다 원문에 있으면 지어낸 것이 아니다."""
    key = _quote_key(source)
    lines = [line for line in map(_quote_key, quote.splitlines()) if line]
    return bool(lines) and all(line in key or _mostly_in(line, key) for line in lines)


def _mostly_in(line: str, key: str) -> bool:
    """사진에서 옮겨 적은 글자는 한두 자 다를 수 있다. 지어낸 문장은 이만큼 겹치지 않는다.

    두 글자 이하 조각은 세지 않는다. 긴 원문에서는 아무 문장이나 그 정도는 겹친다.
    """
    blocks = SequenceMatcher(None, line, key, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 3) >= len(line) * QUOTE_OVERLAP


def content_hash(text: str) -> str:
    """띄어쓰기가 달라도 같은 공지다 (OCR 은 읽을 때마다 공백이 다르다)."""
    return hashlib.sha256(_squeeze(text).encode()).hexdigest()


def _own_orgs(names: list[str]) -> tuple[set[str], set[str]]:
    """(온전한 이름, 약칭). "무지개유치원 운영위원회" 는 "무지개유치원" 까지만 가린다.

    약칭은 모델이 목록에 따로 준 것 중 온전한 이름의 앞부분과, 온전한 이름의 앞부분 그 자체다.
    다른 기관 · 반 이름은 남는다.
    """
    cleaned = {n.strip() for n in names if len(n.strip()) >= 2}
    full = {m[0].strip() for n in cleaned if (m := _OWN_ORG.match(n))} - _GENERIC_ORGS
    keys = [_squeeze(name) for name in full]
    short = {
        n
        for n in cleaned
        if not _OWN_ORG.match(n) and any(key.startswith(_squeeze(n)) for key in keys)
    }
    stems = {_ORG_SUFFIX.sub("", name) for name in full}
    return full, short | {stem for stem in stems if len(stem) >= _MIN_ORG_STEM}


def _mask_word(text: str, name: str, mark: str) -> str:
    return re.sub(re.escape(name) + _NAME_TAIL, mark, text)


def same_notice(left: str, right: str) -> bool:
    """사진은 읽을 때마다 글자가 조금씩 달라 해시로는 같은 공지를 못 찾는다.

    🚨 숫자가 하나라도 다르면 다른 공지다. 달마다 같은 양식으로 나오는 공지와 날짜만 바꾼 공지는
       글자가 97% 넘게 같다. 합쳐 버리면 뒤의 공지가 사라지는데, 두 번 저장되는 쪽이 덜 나쁘다.
    """
    left, right = _quote_key(left), _quote_key(right)  # 표 구분 기호 · 구분선 길이는 글자가 아니다
    if sorted(_DIGITS.findall(left)) != sorted(_DIGITS.findall(right)):
        return False
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    return matcher.quick_ratio() >= SAME_NOTICE_RATIO and matcher.ratio() >= SAME_NOTICE_RATIO


def mask_names(text: str, *, people: list[str], institutions: list[str]) -> str:
    """사람 · 다니는 기관 이름 · 전화번호를 가린다. 긴 이름부터 — "김가나다" 안의 "가나" 때문."""
    masked = _PHONE.sub("[전화]", text)
    full, short = _own_orgs(institutions)
    for name in sorted(full, key=len, reverse=True):
        masked = masked.replace(name, "[기관]")
    for name in sorted(short, key=len, reverse=True):
        masked = _mask_word(masked, name, "[기관]")  # "해님" 이 "해님반" 을 지우면 안 된다
    for name in sorted({n.strip() for n in people if len(n.strip()) >= 2}, key=len, reverse=True):
        if len(name) > _SHORT_NAME:
            masked = masked.replace(name, "[이름]")
        else:
            masked = _mask_word(masked, name, "[이름]")
    return masked


def _at(day: date, moment: time | None, tz: tzinfo) -> datetime:
    return datetime.combine(day, moment or time(0, 0), tzinfo=tz)


def _span(when: NoticeWhen, tz: tzinfo) -> tuple[datetime | None, datetime | None, bool]:
    """(시작, 끝, 종일). 종일은 일정 쪽 관례(00:00 ~ 23:59)와 같다."""
    if when.start is None:
        return None, None, False
    all_day = when.start_time is None
    starts_at = _at(when.start, when.start_time, tz)
    if all_day:
        return starts_at, _at(when.end or when.start, _END_OF_DAY, tz), True
    if when.end is not None:
        ends_at = _at(when.end, when.end_time or _END_OF_DAY, tz)
    elif when.end_time is not None:
        ends_at = _at(when.start, when.end_time, tz)
    else:
        ends_at = None
    if ends_at is not None and ends_at <= starts_at:
        ends_at = None
    return starts_at, ends_at, False


def _cleaned(raw: ExtractedItem) -> ExtractedItem:
    """모델이 낸 글자도 파일에서 꺼낸 글자와 같은 정리를 지난다."""

    def text(value: str | None) -> str | None:
        return clean_text(value) if value is not None else None

    return raw.model_copy(
        update={
            "label": clean_text(raw.label),
            "title": clean_text(raw.title),
            "date_text": text(raw.date_text),
            "due_text": text(raw.due_text),
            "details": [
                ItemDetail(name=clean_text(d.name), value=clean_text(d.value)) for d in raw.details
            ],
            "source_quote": clean_text(raw.source_quote),
            "supplies": [clean_text(name) for name in raw.supplies],
        }
    )


def _source_words(source: str) -> frozenset[str]:
    """원문의 라틴 문자 낱말. 전각 글자는 반각으로 맞춘다.

    사진의 "ＺＯＯＭ" 을 모델은 "ZOOM" 으로 적는다.
    """
    return frozenset(w.lower() for w in _LATIN_RUN.findall(unicodedata.normalize("NFKC", source)))


def _monologue(text: str, words: frozenset[str]) -> bool:
    """원문에 없는 라틴 문자 낱말이 둘 이상인가. 낱말 단위로 대조하고 대소문자는 가리지 않는다.

    부분 문자열로 대조하면 원문의 "information" 때문에 "Info" 가 통과한다.
    """
    found = _LATIN_WORD.findall(unicodedata.normalize("NFKC", text))
    return sum(w.lower() not in words for w in found) >= _MONOLOGUE_WORDS


def _title(raw: ExtractedItem, words: frozenset[str]) -> tuple[str, bool]:
    """(제목, 확인 필요). 혼잣말이 섞이면 첫 여는 괄호 앞까지만 남긴다.

    혼잣말은 제목 뒤에 덧붙인 괄호에 든다. 그래도 남으면 확인을 요청한다. 괄호로 시작하는
    제목("(필독) …")은 괄호 앞이 비므로 자르지 않고 확인만 요청한다.
    """
    title = raw.title.strip() or raw.label.strip() or "안내"
    if not _monologue(title, words):
        return title, False
    cut = _OPEN_PAREN.split(title, maxsplit=1)[0].strip()
    if not cut:
        return title, True
    return cut, _monologue(cut, words)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _details(raw: list[ItemDetail], hide: Callable[[str], str]) -> tuple[ItemDetail, ...]:
    """빈 값 · 중복을 빼고 개수 · 길이를 묶는다. 값은 가린 뒤에 자른다 — 이름이 잘려 남지 않게."""
    kept: dict[tuple[str, str], ItemDetail] = {}
    for detail in raw:
        value = hide(detail.value.strip())
        if not value:
            continue
        name = hide(detail.name.strip()) or "안내"
        kept.setdefault(
            (name, value),
            ItemDetail(name=_cut(name, MAX_DETAIL_NAME), value=_cut(value, MAX_DETAIL_VALUE)),
        )
    return tuple(kept.values())[:MAX_DETAILS]


def _written_as(texts: list[str], details: list[ItemDetail]) -> list[ItemDetail]:
    """규칙이 날짜로 쓰지 못한 원문 글 → `적힌 대로` 줄. 값은 고치지 않는다.

    모델이 이미 details 에 적었으면(공백 · 기호를 빼고 같은 글이 들어 있으면) 넣지 않는다.
    """
    have = [_MARKS.sub("", d.value) for d in details]
    lines: list[ItemDetail] = []
    for text in texts:
        key = _MARKS.sub("", text)
        if key and not any(key in value for value in have):
            lines.append(ItemDetail(name=WRITTEN_AS, value=text.strip()))
            have.append(key)
    return lines


def _until_clock(text: str, at: time) -> bool:
    """글에 그 시각의 "까지" 가 있는가. 날짜 규칙과 같게 읽는다 — "9시" 와 "9:00" 은 같다."""
    return any(c.at == at and _UNTIL_AFTER.match(text, c.end) for c in _times(text))


def _until_text(date_text: str, quote: str, at: time, hide: Callable[[str], str]) -> str:
    """일정의 "9시까지" 를 화면에 남길 원문. 무엇의 마감인지(등원 · 집합)는 근거 문장에 있다.

    근거 문장에서 그 시각의 "까지" 가 든 줄을 쓴다. 다른 줄은 다른 이야기일 수 있다. 그 줄이
    길이 상한을 넘으면 잘려 시각이 사라지므로 날짜 칸의 글을 쓴다. `quote` 는 원문 대조를 지난 것만.
    """
    line = next((ln.strip() for ln in quote.splitlines() if _until_clock(ln, at)), "")
    return line if line and len(hide(line)) <= MAX_DETAIL_VALUE else date_text


def _item(
    raw: ExtractedItem,
    *,
    source: str,
    anchor: date,
    base_month: tuple[int, int | None] | None,
    tz: tzinfo,
    people: list[str],
    orgs: list[str],
) -> NewNoticeItem:
    raw = _cleaned(raw)
    reasons: list[str] = []
    # 날짜 칸에서 읽었지만 화면의 "언제" 줄에 나타나지 않게 된 원문 글
    unshown: list[str] = []
    # 일정의 "9시까지" 에서 마감으로 옮긴 시각
    until_at: time | None = None

    def hide(value: str) -> str:
        return mask_names(value, people=people, institutions=orgs)

    # 모델은 할 일의 마감을 일시 칸에 적기도 한다. "9월 내로" 는 어느 칸에 있어도 마감이다
    when = parse_notice_when(
        raw.date_text, anchor=anchor, base_month=base_month, deadline=raw.handling == "todo"
    )
    due = parse_notice_when(raw.due_text, anchor=anchor, base_month=base_month, deadline=True)
    if raw.handling == "info":
        # 안내는 묻지 않는다. 묻지 않을 값이면 남기지 않는다 — 못 믿을 날짜가 화면에 설 자리가 없다
        when = NoticeWhen() if when.review else when
        due = NoticeWhen() if due.review else due
    else:
        reasons.extend(r for r in (when.review, due.review) if r)

    if due.open_ended:
        # "10월 1일 10:00 ~ 선착순 마감" 의 10월 1일은 시작이다. 마감으로 두면 지난 마감으로 보인다
        if when.start is None:
            when = due
        else:
            # 일시 칸의 시작을 쓰고 이 칸은 버린다. 신청이 열리는 때가 사라지지 않게 원문을 남긴다
            unshown.append(raw.due_text or "")
        due = NoticeWhen()
        if raw.handling == "todo":
            reasons.append("마감을 읽지 못했어요")
    elif (
        raw.handling == "info"
        and not (raw.date_text or "").strip()
        and due.start is not None
        and due.end is not None
    ):
        # 안내의 "10/27 10:00 ~ 11/17 18:00" 을 마감 칸에만 적어도 시작을 버리지 않는다. 일시 칸에
        # 적은 것과 같은 행(기간)이 되게 한다. 안내만이다 —
        # - 일정의 마감 칸 기간은 대개 신청 · 회신 기간이라 행사 기간으로 바꾸면 틀린 일정이 확인
        #   없이 들어간다. 전처럼 시작을 되묻는다
        # - 할 일은 마감이 분명히 보여야 한다. 시작이 생기면 화면 이름표가 "마감" 에서 "언제" 로
        #   바뀌고 보호자가 날짜를 고쳐도 마감이 따라 바뀌지 않는다. 전처럼 끝 날 마감만 둔다
        when, due = due, NoticeWhen()
    # 할 일에 날짜만 있으면 그 날이 마감이다. 그래야 "이번 주 마감" 에 잡힌다. 다만 "신청 시작일시"
    # 는 열리는 시각이다 — 마감으로 두면 선착순이 열리는 날을 끝나는 날로 알려 준다
    opens = _OPENS.search(f"{raw.date_text or ''} {raw.source_quote}")
    if raw.handling == "todo" and due.start is None and not when.open_ended and not opens:
        due = when
        if when.end is None:
            # 모델은 같은 "내일" 을 일시 칸에도 마감 칸에도 적는다. 어느 쪽이든 같은 행이 되게 한다
            when = NoticeWhen()
    if raw.handling == "calendar" and when.until and when.start_time is not None:
        # "8/27(목) 9시까지 등원" 의 까지는 시각에 붙는다. 그날 9시까지 오라는 뜻이라 일정은 그날
        # 하루이고, 9시는 시작이 아니라 그 일의 마감이다. 시작 시각을 지어내지 않는다
        due = when if due.start is None else due
        # 확인 화면은 일정의 시작만 보여 준다. "9시까지 등원" 이 사라지지 않게 원문을 남긴다
        until_at = when.start_time
        when = replace(when, start_time=None, end_time=None, until=False)
    elif when.until:
        # "4월 30일까지 시행" 을 시작으로 읽으면 없던 하루짜리 일정이 생긴다
        due = when if due.start is None else due
        when = NoticeWhen()
        if raw.handling == "calendar":
            reasons.append("시작 날짜를 찾지 못했어요")
    if raw.handling != "info" and (when.vague_time or due.vague_time):
        # 시각은 비우고 날짜만 남겼다. 안내는 묻지 않으므로 날짜만 보여 준다
        reasons.append("오전인지 오후인지 적혀 있지 않아요")

    starts_at, ends_at, all_day = _span(when, tz)
    if raw.handling == "calendar" and starts_at is None and not reasons:
        reasons.append("날짜를 찾지 못했어요")

    # 마감은 범위의 끝 날이다 — "11/3 ~ 11/7 까지 회신". 끝 날에 시각이 없으면 그날 안이다
    due_day = due.end or due.start
    due_time = due.end_time or (None if due.end else due.start_time)
    due_at = _at(due_day, due_time or _END_OF_DAY, tz) if due_day else None

    quote = raw.source_quote.strip()
    quoted = _quoted(quote, source)
    if not quoted:
        reasons.append("근거 문장을 원문에서 찾지 못했어요")

    if (
        raw.handling != "info"
        and starts_at is None
        and due_at is None
        and not reasons
        and any(_DATE_IN_VALUE.search(detail.value) for detail in raw.details)
    ):
        # details 는 날짜 규칙을 지나지 않는다. 일시가 그쪽으로 새면 일정 · 마감에서 사라진다
        reasons.append("날짜를 항목에 넣지 못했어요")
    if (
        raw.handling == "calendar"
        and all_day
        and not reasons
        and any(
            _TIME_NAME.match(detail.name) and _TIME_IN_VALUE.search(detail.value)
            for detail in raw.details
        )
    ):
        reasons.append("시각을 항목에 넣지 못했어요")
    # 날짜 검사가 끝난 뒤에 본다 — 앞의 검사들은 "다른 이유가 없을 때만" 붙는다
    words = _source_words(source)
    title, odd_title = _title(raw, words)
    if odd_title:
        reasons.append("제목에 공지에 없는 글이 섞였어요")
    # details 는 보여 주기만 하는 값이라 줄을 버려도 일정 · 마감이 바뀌지 않는다
    details = [d for d in raw.details if not _monologue(d.value, words)]
    # 위의 확인 검사 · 혼잣말 거르기가 끝난 뒤에 붙인다 — 날짜 칸의 원문이라 둘 다의 대상이 아니다.
    # 앞에 두어 개수 상한에 잘리지 않게 한다
    # 모델이 그 시각의 "까지" 를 details 에 적었으면 화면에 이미 있다 ("9시까지 등교 부탁드리며")
    if until_at is not None and not any(_until_clock(d.value, until_at) for d in details):
        unshown.insert(0, _until_text(raw.date_text or "", quote if quoted else "", until_at, hide))
    details = [*_written_as(unshown, details), *details]

    return NewNoticeItem(
        handling=NoticeItemHandling(raw.handling),
        label=hide(raw.label.strip()) or "안내",
        title=hide(title),
        target_scope=NoticeItemTarget(raw.target_scope),
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        due_at=due_at,
        due_all_day=due_day is not None and due_time is None,
        details=_details(details, hide),
        supplies=tuple(hide(i.item_name) for i in draft_items([raw.supplies])),
        source_quote=hide(quote),
        needs_review=bool(reasons),
        review_reason=" · ".join(reasons) or None,
    )


def assemble(
    extract: NoticeExtract,
    *,
    source_text: str,
    input_format: NoticeInputFormat,
    received_at: datetime,
    tz: tzinfo,
    extractor_version: str | None = None,
) -> NewNotice:
    """`source_text` 는 가리기 전 글자다. 근거 문장 대조는 가리기 전에 해야 맞는다."""
    source_text = clean_text(source_text)
    received_on = received_at.astimezone(tz).date()
    issued = parse_notice_when(extract.issued_text, anchor=received_on).start
    anchor = issued or received_on
    base_month = parse_base_month(extract.base_month_text)
    people = [clean_text(name) for name in extract.person_names]
    orgs = [clean_text(name) for name in extract.institution_names]
    title = clean_text(extract.title) if extract.title else None
    return NewNotice(
        doc_kind=NoticeDocKind(extract.doc_kind),
        input_format=input_format,
        audience_scope=NoticeAudience(extract.audience_scope),
        issued_on=issued,
        received_at=received_at.astimezone(UTC),
        title=mask_names(title, people=people, institutions=orgs) if title else None,
        raw_text=mask_names(source_text, people=people, institutions=orgs),
        content_hash=content_hash(source_text),
        is_correction=extract.is_correction,
        items=tuple(
            _item(
                raw,
                source=source_text,
                anchor=anchor,
                base_month=base_month,
                tz=tz,
                people=people,
                orgs=orgs,
            )
            for raw in extract.items
        ),
        extractor_version=extractor_version,
    )


def to_event_draft(item: NewNoticeItem) -> CreateEventDraft | None:
    """`calendar` 항목 → 일정 초안. 날짜가 없으면 초안을 만들지 않는다. 🚨 저장하지 않는다."""
    if item.handling is not NoticeItemHandling.CALENDAR or item.starts_at is None:
        return None
    return CreateEventDraft(
        draft_id=None,
        op="create",
        event=EventBody(
            title=item.title[:MAX_EVENT_TITLE],
            starts_at=item.starts_at,
            ends_at=item.ends_at,
            all_day=item.all_day,
            event_type="episodic",
            category="institution",
        ),
        items=[DraftItemPayload(item_id=None, item_name=name) for name in item.supplies],
    )
