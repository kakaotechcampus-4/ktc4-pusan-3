"""알림장 · 공지 OCR 채점 — 지어낸 공지 7장(정답을 안다)을 읽어 판정표와 비교한다.

    uv run python -m scripts.notice_fixture_eval --repeat 5
    uv run python -m scripts.notice_fixture_eval --only H --repeat 1   # 한 장만

공지 사진 → `NoticeExtractor.read_image`(모델) → `assemble`(날짜 계산 · 가리기, 코드) 까지
운영과 같은 길로 돌린다. DB 에는 쓰지 않는다. 평가셋은 `tests/fixtures/notice_eval/` — 이름 ·
기관 · 번호가 전부 지어낸 것이라 저장소에 둔다. 실제 기관 공지 말뭉치는 `notice_corpus_eval.py`
(저장소 밖).

C1 (docs/agents/eval/NOTICE_OCR_EVAL.md §2) — 확인 표시(`needs_review`) 없이 틀린 일정 · 할 일 날짜,
기관 안내를 아이의 진단으로 옮김. 날짜가 비었거나 확인 표시가 붙은 오답은 일반 실패다(보호자가
채우거나 고친다). 입력 하나 = 판정 하나. C1 비율의 분모는 C1 이 날 수 있는 판정(날짜 · 진단)이고,
분류 · 가리기 · 금액 같은 일반 판정은 안정 오답 · 불안정에만 센다.
"""

import argparse
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

from app.core.config import settings
from app.domains.notice.assemble import NewNotice, NewNoticeItem, assemble
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItemHandling,
)
from app.providers.notice.document_text import normalize_image
from app.providers.notice.extractor import NoticeExtractor
from app.rules.notice_text import clean_text

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "notice_eval"
KST = ZoneInfo("Asia/Seoul")

CALENDAR, TODO = NoticeItemHandling.CALENDAR, NoticeItemHandling.TODO


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


def on_date(
    items: Sequence[NewNoticeItem],
    keyword: str,
    field: str,
    expect: str,
) -> Outcome:
    """`keyword` 를 다룬 항목 중 하나의 `field` 가 `expect`(앞부분 일치)면 통과.

    맞는 항목이 없을 때 — 확인 표시 없이 다른 날짜가 들어간 항목이 있으면 C1, 아니면(비었거나 표시가
    붙었거나 항목이 없으면) 일반 실패.
    """
    found = [i for i in items if keyword in _about(i)]
    values = [(i, _at(getattr(i, field))) for i in found]
    if any(v.startswith(expect) for _, v in values):
        return Outcome.PASS
    if any(v and not i.needs_review for i, v in values):
        return Outcome.C1
    return Outcome.FAIL


def never_on(items: Sequence[NewNoticeItem], field: str, text: str) -> Outcome:
    """일정 · 할 일의 `field` 에 `text`(날짜 · 시각)가 나오면 안 된다.

    우천 연기일 · 정정 전 옛 날짜 · 새벽 3시 같은 것. 표시 없이 나오면 C1, 표시가 붙으면 일반 실패.
    """
    hit = [i for i in items if i.handling in (CALENDAR, TODO) and text in _at(getattr(i, field))]
    if not hit:
        return Outcome.PASS
    return Outcome.FAIL if all(i.needs_review for i in hit) else Outcome.C1


_DIAGNOSIS = re.compile(r"감기|독감|질환|진단|의심|확진|감염")


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


# 11-02 판정(41개)에서 옮기고 학원 공지를 더했다. 날짜는 전부 받은 날 기준으로 코드가 계산한 값이다
CHECKS: tuple[Check, ...] = (
    # A 문자
    _plain("A", "문자로 분류", lambda n: n.doc_kind is NoticeDocKind.MESSAGE),
    _plain("A", "아이 · 담임 이름 없음", lambda n: not re.search("김하늘|박라온", all_text(n))),
    _plain("A", "원 이름 · 약칭 없음", lambda n: "해솔나무" not in all_text(n)),
    _plain("A", "전화 없음", lambda n: "010-0000" not in all_text(n)),
    _c1("A", "차량 안내 11/5", lambda n: on_date(n.items, "차량", "starts_at", "2026-11-05")),
    _c1("A", "3시를 새벽으로 안 읽음", lambda n: _no_3am(n)),
    _c1("A", "미열을 병명으로 안 바꿈", lambda n: no_diagnosis(n.items)),
    # B 알림장
    _plain(
        "B",
        "알림장 · 아이 대상",
        lambda n: (
            n.doc_kind is NoticeDocKind.DAILY_NOTE and n.audience_scope is NoticeAudience.CHILD
        ),
    ),
    _plain("B", "아이 · 담임 이름 없음", lambda n: not re.search("민준|박라온", all_text(n))),
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
    _c1("D", "숲 체험 11/20 9:30", lambda n: _forest(n)),
    _c1(
        "D", "옛 날짜 11/13 이 일정이 안 됨", lambda n: never_on(n.items, "starts_at", "2026-11-13")
    ),
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
        lambda n: any(i.handling is TODO for i in n.items if "검진" in i.title + i.label),
    ),
    _c1("E", "검진 마감 9/30", lambda n: on_date(n.items, "검진", "due_at", "2026-09-30")),
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
    # H 학원
    _c1(
        "H",
        "음악회 11/21 10시",
        lambda n: on_date(n.items, "음악회", "starts_at", "2026-11-21 10:00"),
    ),
    _c1(
        "H",
        "우천 연기일 11/28 이 일정이 안 됨",
        lambda n: never_on(n.items, "starts_at", "2026-11-28"),
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


def _no_3am(n: NewNotice) -> Outcome:
    worst = [never_on(n.items, field, " 03:00") for field in ("starts_at", "due_at")]
    return Outcome.C1 if Outcome.C1 in worst else min(worst, key=list(Outcome).index)


def _forest(n: NewNotice) -> Outcome:
    return on_date(_calendar(n), "", "starts_at", "2026-11-20 09:30")


def _three_classes(n: NewNotice) -> Outcome:
    """세 반 날짜가 모두 일정에 있어야 한다. 그 밖의 날짜가 표시 없이 있으면 C1."""
    want = {"2026-11-17", "2026-11-18", "2026-11-19"}
    cal = _calendar(n)
    if any(_at(i.starts_at)[:10] not in want and i.starts_at and not i.needs_review for i in cal):
        return Outcome.C1
    return holds(want <= {_at(i.starts_at)[:10] for i in cal})


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


@dataclass
class Read:
    doc: Doc
    run: int
    notice: NewNotice | None
    error: str | None
    seconds: float
    prompt_tokens: int
    completion_tokens: int


def read_doc(doc: Doc, run: int, effort: str) -> Read:
    ex = NoticeExtractor.from_settings(
        settings, reasoning_effort=effort, max_calls=1, timeout_s=170.0
    )
    t0 = time.perf_counter()
    notice, error = None, None
    try:
        image, mime = normalize_image((FIXTURE_DIR / f"{doc.file}.png").read_bytes())
        extract = ex.read_image(image, mime_type=mime)
        received = datetime(doc.received.year, doc.received.month, doc.received.day, 9, tzinfo=KST)
        notice = assemble(
            extract,
            source_text=clean_text(extract.transcript),
            input_format=NoticeInputFormat.IMAGE,
            received_at=received,
            tz=KST,
            extractor_version=ex.version,
        )
    except Exception as err:  # 읽기 실패는 그 문서 판정 전부 일반 실패 — 틀린 일정이 남지 않는다
        error = f"{type(err).__name__}: {str(err)[:150]}"
    return Read(
        doc=doc,
        run=run,
        notice=notice,
        error=error,
        seconds=round(time.perf_counter() - t0, 1),
        prompt_tokens=sum(u.prompt_tokens for u in ex.usage),
        completion_tokens=sum(u.completion_tokens for u in ex.usage),
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repeat", type=int, default=1, help="같은 공지를 몇 번 읽나 (측정 이력은 5)")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--only", help="문서 id 만 (쉼표로 여러 개, 예: H 또는 A,H)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None, help="모델 출력 폴더 (기본: 임시 폴더)")
    args = ap.parse_args()
    if args.repeat < 1:
        ap.error("--repeat 는 1 이상")
    if not settings.MEAL_OCR_BASE_URL or not settings.MEAL_OCR_API_KEY:
        print(
            "apps/api/.env 에 MEAL_OCR_BASE_URL 과 MEAL_OCR_API_KEY 가 필요합니다", file=sys.stderr
        )
        return 2

    only = set(args.only.split(",")) if args.only else None
    docs = [d for d in DOCS if only is None or d.id in only]
    jobs = [(d, r) for r in range(1, args.repeat + 1) for d in docs]
    print(
        f"공지 {len(docs)}장 × 반복 {args.repeat}회 = 호출 {len(jobs)}회"
        f" · 모델 {settings.MEAL_OCR_MODEL} · effort {args.effort}"
    )
    with ThreadPoolExecutor(args.workers) as pool:
        reads = list(pool.map(lambda job: read_doc(job[0], job[1], args.effort), jobs))

    out_dir = args.out or Path(tempfile.gettempdir()) / "notice_fixture_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"notice_{settings.MEAL_OCR_MODEL.replace('/', '_')}_{args.effort}.json"
    out.write_text(
        json.dumps([_dump(r) for r in reads], ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    errors = [r for r in reads if r.error]
    tokens_in = sum(r.prompt_tokens for r in reads)
    tokens_out = sum(r.completion_tokens for r in reads)
    print(f"읽기 실패 {len(errors)} · 입력 {tokens_in} · 출력 {tokens_out} 토큰 · 저장 {out}")
    for r in errors:
        print(f"  {r.doc.id} #{r.run} {r.error}")

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
    )
    meta["repetitions"] = args.repeat
    meta["models"]["notice_ocr"]["reasoning_effort"] = args.effort
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n실행 메타: {META_PATH.relative_to(ROOT)} — commit {meta['commit']}"
        f" · dirty {meta['dirty']} · 지문 {meta['prompts']['notice_ocr'][:12]}"
    )


if __name__ == "__main__":
    sys.exit(main())
