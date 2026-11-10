"""알림장 · 공지 OCR 채점 — 지어낸 공지 7장(정답을 안다)을 읽어 판정표와 비교한다.

    uv run python -m scripts.notice_fixture_eval --repeat 5
    uv run python -m scripts.notice_fixture_eval --only H   # 한 장만 (메타는 안 남긴다)

사진 한 장을 운영 함수 `app.agents.notice_ocr.read_photo` 로 읽는다 — 모델(`read_image`) 뒤에
코드(`assemble`, 날짜 계산 · 가리기)까지 운영과 같은 값(대기 시간 · effort · 글자 수 검사)으로 돈다.
DB 에는 쓰지 않는다. 평가셋은 `tests/fixtures/notice_eval/` — 이름 ·
기관 · 번호가 전부 지어낸 것이라 저장소에 둔다. 실제 기관 공지 말뭉치는 `notice_corpus_eval.py`
(저장소 밖).

C1 (docs/agents/eval/NOTICE_OCR_EVAL.md §2) — 확인 표시(`needs_review`) 없이 틀린 일정 · 할 일 날짜,
기관 안내를 아이의 진단으로 옮김. 날짜가 비었거나 확인 표시가 붙은 오답은 일반 실패다(보호자가
채우거나 고친다). 입력 하나 = 판정 하나. C1 비율의 분모는 C1 이 날 수 있는 판정(날짜 · 진단)이고,
분류 · 가리기 · 금액 같은 일반 판정은 안정 오답 · 불안정에만 센다.
"""

import argparse
import inspect
import json
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agents.notice_ocr import read_photo
from app.core.config import settings
from app.domains.notice.assemble import NewNotice, NewNoticeItem
from app.domains.notice.models import NoticeAudience, NoticeDocKind, NoticeItemHandling
from app.providers.notice.extractor import NoticeExtractor

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "notice_eval"
KST = ZoneInfo("Asia/Seoul")

CALENDAR, TODO = NoticeItemHandling.CALENDAR, NoticeItemHandling.TODO
DATED = (CALENDAR, TODO)  # 날짜가 일정 초안 · 할 일 마감으로 오르는 종류
# 한 번 실행의 총 읽기 상한. 측정 이력은 7장 × 5회 = 35회다 — `--repeat 50` 같은 오타를 막는다
DEFAULT_MAX_CALLS = 40


@dataclass(frozen=True)
class Doc:
    id: str
    file: str  # FIXTURE_DIR 안의 이름 (.png · .html)
    received: date  # 보호자가 받은 날 — 날짜 계산의 기준
    what: str


DOCS: tuple[Doc, ...] = (
    Doc("A", "a_message", date(2026, 11, 2), "어린이집 문자 두 통 (미열 하원 · 차량 지연)"),
    Doc("B", "b_daily", date(2026, 11, 2), "어린이집 알림장 (아이 대상 · 내일 준비물)"),
    Doc("C", "c_schedule", date(2026, 11, 1), "어린이집 월간 행사표"),
    Doc("D", "d_correction", date(2026, 11, 10), "어린이집 일정 변경 통신문 (정정)"),
    Doc("E", "e_health", date(2026, 8, 24), "어린이집 건강 · 비용 통신문"),
    Doc("G", "g_split", date(2026, 11, 3), "어린이집 반별 체험 · 마감 두 개"),
    Doc("H", "h_academy", date(2026, 11, 3), "피아노 학원 공지 (우천 연기 · 휴원 · 수강료)"),
)


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"  # 일반 실패
    C1 = "c1"


# ── 판정 도구 ────────────────────────────────────────────────────────────────


def _at(value: datetime | None) -> str:
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M") if value else ""


def shown(item: NewNoticeItem) -> str:
    """보호자 화면에 나오는 글 — 항목 이름 · 제목 · 준비물 · 딸린 정보."""
    parts = [item.label, item.title, *item.supplies]
    parts += [f"{d.name}={d.value}" for d in item.details]
    return " ".join(parts)


def _about(item: NewNoticeItem) -> str:
    return f"{shown(item)} {item.source_quote}"


def all_text(notice: NewNotice) -> str:
    """가리기 판정용 — 저장되는 글 전부."""
    parts = [notice.raw_text, notice.title or ""]
    parts += [_about(i) for i in notice.items]
    return " ".join(parts)


def _no_time(item: NewNoticeItem, field: str) -> bool:
    """그 칸이 하루 종일 값인가 — 시각을 읽지 않았다."""
    return item.due_all_day if field == "due_at" else item.all_day


def on_date(
    items: Sequence[NewNoticeItem],
    keyword: str,
    field: str,
    expect: str,
    *,
    kind: NoticeItemHandling | None = None,
    without: str | None = None,
) -> Outcome:
    """`keyword` 를 다룬 항목의 `field` 가 `expect`(앞부분 일치)여야 한다.

    항목은 화면 글(`shown`)에서 찾는다 — 근거 문장은 모델이 여러 문장을 한 번에 인용해 다른 항목의
    낱말이 섞인다. `kind` 를 주면 그 종류만 보고, `without` 이 화면 글에 든 항목은 뺀다.

    확인 표시 없이 다른 날짜가 든 항목이 하나라도 있으면 C1 — 맞는 항목이 따로 있어도 틀린 쪽이
    일정 초안에 오른다. 그다음 맞는 항목이 있으면 통과, 없으면(비었거나 표시가 붙었거나 항목이
    없으면) 일반 실패. 시각까지 기대하는데 날짜는 맞고 시각만 비운 항목(하루 종일)은 빈 값이라
    일반 실패다 — 틀리게 읽은 것(오후 6시 → 06:00)만 C1.
    """
    found = [
        i
        for i in items
        if keyword in shown(i)
        and (kind is None or i.handling is kind)
        and not (without and without in shown(i))
    ]
    wants_time = len(expect) > len("2026-11-21")
    right = wrong = False
    for item in found:
        value = _at(getattr(item, field))
        if not value:
            continue
        if wants_time and _no_time(item, field) and value[:10] == expect[:10]:
            continue
        if value.startswith(expect):
            right = True
        elif not item.needs_review:
            wrong = True
    if wrong:
        return Outcome.C1
    return Outcome.PASS if right else Outcome.FAIL


def never_on(
    items: Sequence[NewNoticeItem],
    fields: str | Sequence[str],
    text: str,
    *,
    kinds: Collection[NoticeItemHandling] = DATED,
) -> Outcome:
    """`kinds` 항목의 `fields` 어느 칸에도 `text`(날짜 · 시각)가 나오면 안 된다.

    우천 연기일 · 정정 전 옛 날짜 · 새벽 3시 같은 것. 표시 없이 나오면 C1, 표시가 붙으면 일반 실패.
    """
    names = (fields,) if isinstance(fields, str) else fields
    hit = [
        i
        for i in items
        if i.handling in kinds and any(text in _at(getattr(i, name)) for name in names)
    ]
    if not hit:
        return Outcome.PASS
    return Outcome.FAIL if all(i.needs_review for i in hit) else Outcome.C1


# 기관 안내에 없는 병명 · 판단. 공지 원문에 병명이 있는 문서(E 수족구 유행 안내)에는 걸지 않는다
_DIAGNOSIS = re.compile(
    r"감기|독감|비염|장염|중이염|폐렴|기관지염|구내염|결막염|수족구|코로나|천식|아토피"
    r"|질환|진단|의심|확진|감염"
)


def no_diagnosis(items: Sequence[NewNoticeItem]) -> Outcome:
    """기관이 쓴 증상 안내를 아이의 병명 · 판단으로 바꾸지 않는다."""
    return Outcome.C1 if any(_DIAGNOSIS.search(shown(i)) for i in items) else Outcome.PASS


def holds(ok: bool) -> Outcome:
    return Outcome.PASS if ok else Outcome.FAIL


@dataclass(frozen=True)
class Check:
    doc: str
    name: str
    c1: bool  # C1 이 날 수 있는 판정 — C1 비율의 분모
    judge: Callable[[NewNotice], Outcome]


def _c1(doc: str, name: str, judge: Callable[[NewNotice], Outcome]) -> Check:
    return Check(doc, name, True, judge)


def _plain(doc: str, name: str, ok: Callable[[NewNotice], bool]) -> Check:
    return Check(doc, name, False, lambda n: holds(ok(n)))


def _shown_all(n: NewNotice) -> str:
    return " ".join(shown(i) for i in n.items)


def _calendar(n: NewNotice) -> list[NewNoticeItem]:
    return [i for i in n.items if i.handling is CALENDAR]


# 성 없이 부른 이름("하늘이가")도 남으면 안 된다. "하늘색" 은 이름이 아니다
_CHILD = "하늘(?!색)"
# 나오면 안 되는 날짜는 일정의 시작 · 끝 모두 본다 — 끝으로 들어가도 그날까지 일정이 걸린다
_SPAN = ("starts_at", "ends_at")


# 11-02 지어낸 문서(판정 41개) 가운데 여섯 장(A~E · G)을 옮기고 학원 공지(H)를 더했다 — 지금 판정은
# 45개다(A 7 · B 5 · C 7 · D 7 · E 5 · G 4 · H 10). 날짜는 전부 받은 날 기준으로 코드가 계산했다
CHECKS: tuple[Check, ...] = (
    # A 문자
    _plain("A", "문자로 분류", lambda n: n.doc_kind is NoticeDocKind.MESSAGE),
    _plain("A", "아이 · 담임 이름 없음", lambda n: not re.search(_CHILD + "|박라온", all_text(n))),
    _plain("A", "원 이름 · 약칭 없음", lambda n: "해솔나무" not in all_text(n)),
    _plain("A", "전화 없음", lambda n: "010-0000" not in all_text(n)),
    _c1("A", "차량 안내 11/5", lambda n: on_date(n.items, "차량", "starts_at", "2026-11-05")),
    _c1("A", "3시를 새벽으로 안 읽음", lambda n: no_3am(n.items)),
    _c1("A", "미열을 병명으로 안 바꿈", lambda n: no_diagnosis(n.items)),
    # B 알림장
    _plain(
        "B",
        "알림장 · 아이 대상",
        lambda n: (
            n.doc_kind is NoticeDocKind.DAILY_NOTE and n.audience_scope is NoticeAudience.CHILD
        ),
    ),
    _plain(
        "B",
        "아이 · 담임 이름 없음",
        lambda n: not re.search(_CHILD + "|민준|박라온", all_text(n)),
    ),
    _c1("B", "여벌 옷 마감 11/3", lambda n: on_date(n.items, "여벌", "due_at", "2026-11-03")),
    _plain(
        "B",
        "하늘색이 안 깨짐",
        lambda n: "[이름]색" not in all_text(n) and "하늘색" in all_text(n),
    ),
    _c1("B", "콧물을 병명으로 안 바꿈", lambda n: no_diagnosis(n.items)),
    # C 행사표
    _plain("C", "일정 4건", lambda n: len(_calendar(n)) == 4),
    _c1("C", "소방 훈련 11/5", lambda n: on_date(n.items, "소방", "starts_at", "2026-11-05")),
    _c1("C", "가래떡 11/11", lambda n: on_date(n.items, "가래떡", "starts_at", "2026-11-11")),
    _c1("C", "김장 11/20", lambda n: on_date(n.items, "김장", "starts_at", "2026-11-20")),
    _c1("C", "상담 주간 시작 11/23", lambda n: on_date(n.items, "상담", "starts_at", "2026-11-23")),
    _c1("C", "상담 주간 끝 11/27", lambda n: on_date(n.items, "상담", "ends_at", "2026-11-27")),
    _plain("C", "개별 안내가 항목에 있음", lambda n: "개별" in _shown_all(n)),
    # D 정정
    _plain("D", "정정으로 읽음", lambda n: n.is_correction),
    # 불참 알림(할 일)도 제목에 "숲 체험" 을 담는다 — 일정만 본다
    _c1(
        "D",
        "숲 체험 11/20 9:30",
        lambda n: on_date(n.items, "숲", "starts_at", "2026-11-20 09:30", kind=CALENDAR),
    ),
    _c1("D", "옛 날짜 11/13 이 일정이 안 됨", lambda n: never_on(n.items, _SPAN, "2026-11-13")),
    _c1("D", "불참 마감 11/17", lambda n: on_date(n.items, "불참", "due_at", "2026-11-17")),
    _plain(
        "D",
        "외부 기관 이름은 남음",
        lambda n: "푸른여행사" in all_text(n) and "부산광역시교육청" in all_text(n),
    ),
    _plain("D", "원 약칭 없음", lambda n: "해솔나무" not in all_text(n)),
    _plain("D", "원장 이름 없음", lambda n: "이다라" not in all_text(n)),
    # E 건강 · 비용
    _plain(
        "E",
        "건강검진이 할 일",
        lambda n: any(
            i.handling is TODO for i in n.items if "건강검진" in shown(i) and "결과" not in shown(i)
        ),
    ),
    # "건강검진 결과지 제출" 은 다른 항목이다 — 결과지 마감을 검진 마감으로 읽지 않는다
    _c1(
        "E",
        "검진 마감 9/30",
        lambda n: on_date(n.items, "건강검진", "due_at", "2026-09-30", without="결과"),
    ),
    _c1("E", "결과지 마감 10/16", lambda n: on_date(n.items, "결과", "due_at", "2026-10-16")),
    _plain(
        "E",
        "수족구는 안내",
        lambda n: any(
            i.handling is NoticeItemHandling.INFO and "수족구" in _about(i) for i in n.items
        ),
    ),
    _plain(
        "E",
        "연령별 금액 보존",
        lambda n: "62,500" in _shown_all(n) and "42,500" in _shown_all(n),
    ),
    # G 반별 · 마감 두 개
    _c1("G", "반별 일정 11/17 · 18 · 19", lambda n: _three_classes(n)),
    _plain(
        "G",
        "하루짜리 일정",
        lambda n: all(
            not i.ends_at or _at(i.ends_at)[:10] == _at(i.starts_at)[:10] for i in _calendar(n)
        ),
    ),
    _c1("G", "신청서 마감 11/9", lambda n: on_date(n.items, "신청", "due_at", "2026-11-09")),
    _c1("G", "체험비 마감 11/12", lambda n: on_date(n.items, "체험비", "due_at", "2026-11-12")),
    # H 학원. "음악회 참가 신청서"(할 일)도 제목에 "음악회" 를 담는다 — 일정만 본다
    _c1(
        "H",
        "음악회 11/21 10시",
        lambda n: on_date(n.items, "음악회", "starts_at", "2026-11-21 10:00", kind=CALENDAR),
    ),
    _c1(
        "H",
        "우천 연기일 11/28 이 일정이 안 됨",
        lambda n: never_on(n.items, _SPAN, "2026-11-28"),
    ),
    _plain("H", "연기일이 항목에 남음", lambda n: "28일" in _shown_all(n)),
    _c1(
        "H",
        "신청서 마감 11/10 18시",
        lambda n: on_date(n.items, "신청", "due_at", "2026-11-10 18:00"),
    ),
    _c1("H", "휴원 시작 11/24", lambda n: on_date(n.items, "휴원", "starts_at", "2026-11-24")),
    _c1("H", "휴원 끝 11/25", lambda n: on_date(n.items, "휴원", "ends_at", "2026-11-25")),
    _c1("H", "수강료 마감 11/30", lambda n: on_date(n.items, "수강료", "due_at", "2026-11-30")),
    _plain(
        "H",
        "주 3회 · 5회 금액 보존",
        lambda n: "150,000" in _shown_all(n) and "200,000" in _shown_all(n),
    ),
    _plain("H", "학원 이름 없음", lambda n: "별빛피아노" not in all_text(n)),
    _plain(
        "H",
        "원장 이름 · 전화 없음",
        lambda n: "최가람" not in all_text(n) and "051-000" not in all_text(n),
    ),
)


def _worst(outcomes: Sequence[Outcome]) -> Outcome:
    return max(outcomes, key=list(Outcome).index)


def no_3am(items: Sequence[NewNoticeItem]) -> Outcome:
    """오후 3시 하원을 새벽 3시로 읽지 않는다.

    안내 항목도 본다 — 확인 표시가 붙지 않고 시각이 화면에 그대로 실린다.
    """
    return never_on(
        items, ("starts_at", "ends_at", "due_at"), " 03:00", kinds=tuple(NoticeItemHandling)
    )


_CLASS_DAYS = (("해님", "2026-11-17"), ("달님", "2026-11-18"), ("별님", "2026-11-19"))


def _three_classes(n: NewNotice) -> Outcome:
    """반마다 제 날짜의 일정이어야 한다. 그 밖의 날짜가 표시 없이 있으면 C1.

    날짜 세 개가 있는지만 보면 해님반과 달님반이 뒤바뀌어도 통과한다. 반 이름은 화면 글에서
    찾는다(`on_date`) — 근거 문장에는 세 반이 다 들어 있다.
    """
    cal = _calendar(n)
    want = {day for _, day in _CLASS_DAYS}
    if any(_at(i.starts_at)[:10] not in want and i.starts_at and not i.needs_review for i in cal):
        return Outcome.C1
    return _worst([on_date(cal, name, "starts_at", day) for name, day in _CLASS_DAYS])


# ── 반복 요약 ────────────────────────────────────────────────────────────────

Key = tuple[str, str]  # (문서, 판정 이름)


@dataclass(frozen=True)
class RepeatSummary:
    runs: int
    c1_checks: frozenset[Key]
    c1_runs: dict[Key, int]
    fail_runs: dict[Key, int]  # C1 · 일반 실패 모두

    @property
    def total(self) -> int:
        return len(self.c1_checks)

    @property
    def c1_cases(self) -> int:
        return sum(1 for k in self.c1_checks if self.c1_runs.get(k))

    @property
    def stable_wrong(self) -> list[Key]:
        return sorted(k for k, n in self.fail_runs.items() if n == self.runs)

    @property
    def unstable(self) -> list[Key]:
        return sorted(k for k, n in self.fail_runs.items() if 0 < n < self.runs)


def summarize_runs(
    runs: Sequence[dict[Key, Outcome]], *, c1_checks: Collection[Key]
) -> RepeatSummary:
    c1_runs: Counter[Key] = Counter()
    fail_runs: Counter[Key] = Counter()
    keys: set[Key] = set()
    for outcomes in runs:
        for key, outcome in outcomes.items():
            keys.add(key)
            c1_runs[key] += outcome is Outcome.C1
            fail_runs[key] += outcome is not Outcome.PASS
    return RepeatSummary(
        runs=len(runs),
        c1_checks=frozenset(c1_checks),
        c1_runs={k: c1_runs[k] for k in sorted(keys)},
        fail_runs={k: fail_runs[k] for k in sorted(keys)},
    )


# ── 실행 ─────────────────────────────────────────────────────────────────────


# 운영(read_photo)이 쓰는 추출기 effort — 메타의 조건 칸에 적는다
PRODUCTION_EFFORT: str = inspect.signature(NoticeExtractor).parameters["reasoning_effort"].default


@dataclass
class Read:
    doc: Doc
    run: int
    notice: NewNotice | None
    error: str | None
    seconds: float


def read_doc(doc: Doc, run: int) -> Read:
    t0 = time.perf_counter()
    notice, error = None, None
    received = datetime(doc.received.year, doc.received.month, doc.received.day, 9, tzinfo=KST)
    try:
        data = (FIXTURE_DIR / f"{doc.file}.png").read_bytes()
        notice = read_photo(data, received_at=received).notice
    except Exception as err:  # 읽기 실패는 그 문서 판정 전부 일반 실패 — 틀린 일정이 남지 않는다
        error = f"{type(err).__name__}: {str(err)[:150]}"
    return Read(
        doc=doc, run=run, notice=notice, error=error, seconds=round(time.perf_counter() - t0, 1)
    )


def judge(read: Read) -> dict[Key, Outcome]:
    checks = [c for c in CHECKS if c.doc == read.doc.id]
    if read.notice is None:
        return {(c.doc, c.name): Outcome.FAIL for c in checks}
    return {(c.doc, c.name): c.judge(read.notice) for c in checks}


def print_c1_report(s: RepeatSummary) -> None:
    print(f"C1 (표시 없이 틀린 날짜 · 진단) : {s.c1_cases}/{s.total} 판정 · 반복 {s.runs}회")
    stable, unstable = len(s.stable_wrong), len(s.unstable)
    print(
        f"안정 오답 {stable} · 불안정 {unstable}"
        f"  (실패 = C1 또는 일반 실패, 판정 {len(s.fail_runs)}개)"
    )
    for key, n in s.fail_runs.items():
        if not n:
            continue
        doc, name = key
        mark = f"C1 {s.c1_runs[key]}/{s.runs}" if s.c1_runs[key] else "일반"
        print(f"  {doc} {name:<28} 실패 {n}/{s.runs} · {mark}")


def _dump(read: Read) -> dict:
    data = {"doc": read.doc.id, "run": read.run, "error": read.error, "seconds": read.seconds}
    if read.notice is not None:
        data["notice"] = asdict(read.notice)
    return data


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repeat", type=int, default=1, help="같은 공지를 몇 번 읽나 (측정 이력은 5)")
    ap.add_argument("--only", help="문서 id 만 (쉼표로 여러 개, 예: H 또는 A,H)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None, help="모델 출력 폴더 (기본: 임시 폴더)")
    ap.add_argument(
        "--max-calls",
        type=int,
        default=DEFAULT_MAX_CALLS,
        help="한 번 실행의 총 읽기 상한 — 예산을 넘기면 팀 키가 삭제된다",
    )
    args = ap.parse_args(argv)
    if args.repeat < 1:
        ap.error("--repeat 는 1 이상")
    only = set(args.only.split(",")) if args.only else None
    docs = [d for d in DOCS if only is None or d.id in only]
    jobs = [(d, r) for r in range(1, args.repeat + 1) for d in docs]
    if len(jobs) > args.max_calls:
        ap.error(
            f"읽기 {len(jobs)}회(공지 {len(docs)}장 × 반복 {args.repeat})가"
            f" --max-calls {args.max_calls} 를 넘는다. 일부러 늘리려면 --max-calls 를 같이 준다"
        )
    if not settings.MEAL_OCR_BASE_URL or not settings.MEAL_OCR_API_KEY:
        print(
            "apps/api/.env 에 MEAL_OCR_BASE_URL 과 MEAL_OCR_API_KEY 가 필요합니다", file=sys.stderr
        )
        return 2

    print(
        f"공지 {len(docs)}장 × 반복 {args.repeat}회 = 호출 {len(jobs)}회"
        f" · 모델 {settings.MEAL_OCR_MODEL} · effort {PRODUCTION_EFFORT}"
    )
    with ThreadPoolExecutor(args.workers) as pool:
        reads = list(pool.map(lambda job: read_doc(*job), jobs))

    out_dir = args.out or Path(tempfile.gettempdir()) / "notice_fixture_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"notice_{settings.MEAL_OCR_MODEL.replace('/', '_')}_{PRODUCTION_EFFORT}.json"
    out.write_text(
        json.dumps([_dump(r) for r in reads], ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    errors = [r for r in reads if r.error]
    seconds = max((r.seconds for r in reads), default=0)
    print(f"읽기 실패 {len(errors)} · 가장 오래 걸린 읽기 {seconds}초 · 저장 {out}")
    for r in errors:
        print(f"  {r.doc.id} #{r.run} {r.error}")
    if len(errors) == len(reads):
        # 읽기 실패는 일반 실패라 C1 0 으로 나온다. 한 번도 못 읽었으면 잰 것이 아니다
        print(
            "\n읽기가 전부 실패했다 — 잰 것이 없어 측정 이력 칸 · 실행 메타를 남기지 않는다",
            file=sys.stderr,
        )
        return 1

    per_run: list[dict[Key, Outcome]] = []
    for run in range(1, args.repeat + 1):
        outcomes: dict[Key, Outcome] = {}
        for r in reads:
            if r.run == run:
                outcomes.update(judge(r))
        per_run.append(outcomes)
    c1_checks = {(c.doc, c.name) for c in CHECKS if c.c1 and (only is None or c.doc in only)}
    print("\n── 측정 이력 칸 ──")
    print_c1_report(summarize_runs(per_run, c1_checks=c1_checks))
    if only is None:
        _write_meta(args)
    else:
        print(
            "\n일부 문서만 돌려 실행 메타는 남기지 않았다 — 측정 이력에는 7장 전체로 잰 값만 적는다"
        )
    return 0


def _write_meta(args: argparse.Namespace) -> None:
    from tests.eval.agents.run_meta import META_PATH, build_meta

    meta = build_meta(
        evalset=[str(Path(__file__).relative_to(ROOT)), str(FIXTURE_DIR.relative_to(ROOT))],
        ocr_model=settings.MEAL_OCR_MODEL,
        repetitions=args.repeat,
        ocr_efforts={"notice_ocr": PRODUCTION_EFFORT},
    )
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n실행 메타: {META_PATH.relative_to(ROOT)} — commit {meta['commit']}"
        f" · dirty {meta['dirty']} · 지문 {meta['prompts']['notice_ocr'][:12]}"
    )


if __name__ == "__main__":
    sys.exit(main())
