"""실제 공지 말뭉치를 공지 파이프라인에 통과시켜 어디서 깨지는지 센다.

    uv run python scripts/notice_corpus_eval.py free <말뭉치> <결과 폴더>
    uv run python scripts/notice_corpus_eval.py llm <말뭉치> <결과 폴더> --max-calls 100

🚨 말뭉치와 결과는 저장소 밖에 둔다. 실제 기관 문서라 공개 저장소에 들어오면 안 된다 (CLAUDE.md §9).
🚨 DB 는 .env 의 DB_* 를 쓴다. 개발 DB 를 쓰지 말고 실험용 DB 를 가리킬 것 — 행을 그대로 남긴다.

free — 모델 없이: 파일 읽기, 날짜 표현 읽기, notice 행 넣기.
llm  — 팀 키로 모델을 부른다. 예산을 넘기면 키가 자동 삭제되므로 --max-calls 를 넘지 않는다.
"""

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time as clock
import uuid
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.api.v1.schemas.suggestions import SubmitEventRequest
from app.core.config import settings
from app.core.notice_extract import NoticeExtract
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.notice.assemble import (
    NewNotice,
    assemble,
    content_hash,
    mask_names,
    to_event_draft,
)
from app.domains.notice.models import (
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
)
from app.domains.notice.repository import save_notice
from app.infra.db.session import async_session_factory, engine
from app.providers.notice.document_text import (
    READERS,
    UnreadableDocument,
    normalize_image,
    pdf_pages_as_png,
)
from app.providers.notice.extractor import NoticeExtractor
from app.rules.notice_dates import parse_notice_when
from app.rules.notice_text import clean_text

logging.getLogger("pypdf").setLevel(logging.CRITICAL)

KST = ZoneInfo("Asia/Seoul")
MIN_IMAGE_BYTES = 8_000  # 게시판 아이콘 · 버튼
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MIN_BODY_CHARS = 30

# 공지에 나오는 날짜 · 시각 표현을 넓게 줍는다. 파서가 읽어야 할 후보다
_DATE_CANDIDATE = re.compile(
    r"(?<![\d./])(?:\d{2,4}\s*[년./-]\s*)?\d{1,2}\s*(?:월|[./])\s*\d{1,2}\s*일?\.?(?![\d/])"
    r"(?:\s*[(（][월화수목금토일](?:요일)?[)）])?"
    r"(?:\s*(?:오전|오후)?\s*\d{1,2}\s*(?::\s*\d{2}|시(?:\s*\d{1,2}\s*분)?))?"
    r"(?:\s*[~∼〜-]\s*(?:\d{1,2}\s*(?:월|[./])\s*)?\d{1,2}\s*일?(?:\s*[(（][월화수목금토일][)）])?)?"
)


@dataclass(frozen=True)
class Doc:
    post: str
    file: str
    ext: str
    path: Path
    title: str
    posted_on: date | None
    topic: str
    family: str


def _posted(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def iter_docs(corpus: Path) -> Iterator[Doc]:
    for manifest in sorted(corpus.glob("*/manifest.jsonl")):
        family = manifest.parent.name
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            # 🚨 못 찾으면 멈춘다. 조용히 건너뛰면 "전부 통과" 가 일부만 본 결과가 된다
            #    (유치원 521건이 `posts/` 접두어 때문에 통째로 빠진 채 숫자가 나온 적이 있다)
            folder = manifest.parent / "posts" / Path(row["post_dir"]).name
            if not folder.is_dir():
                raise FileNotFoundError(f"manifest 에 있는데 폴더가 없다: {folder}")
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.name.startswith("."):
                    continue
                yield Doc(
                    post=f"{family}/{row['post_dir']}",
                    file=path.name,
                    ext=path.suffix.lower(),
                    path=path,
                    title=row.get("title") or "",
                    posted_on=_posted(row.get("posted_on")),
                    topic=row.get("topic_guess") or "기타",
                    family=family,
                )


def read_doc(doc: Doc) -> dict:
    """글자를 꺼낸다. 결과: status = text / vision / empty / unreadable / unsupported."""
    base = {"post": doc.post, "file": doc.file, "ext": doc.ext, "family": doc.family}
    if doc.path.name == "body.txt":
        text = clean_text(doc.path.read_text(encoding="utf-8", errors="replace"))
        status = "text" if len(text) >= MIN_BODY_CHARS else "empty"
        return {**base, "status": status, "chars": len(text), "text": text, "pages": 1}
    if doc.ext in IMAGE_EXT:
        status = "vision" if doc.path.stat().st_size >= MIN_IMAGE_BYTES else "empty"
        return {**base, "status": status, "chars": 0, "text": "", "pages": 1}
    reader = READERS.get(doc.ext)
    if reader is None:
        return {**base, "status": "unsupported", "chars": 0, "text": "", "pages": 0}
    started = clock.perf_counter()
    try:
        result = reader(doc.path.read_bytes())
    except UnreadableDocument as e:
        return {**base, "status": "unreadable", "reason": e.reason, "chars": 0, "text": ""}
    except (
        Exception
    ) as e:  # 읽기 코드가 처음 보는 파일에 터지는 것을 세는 것이 이 스크립트의 일이다
        return {**base, "status": "crash", "reason": type(e).__name__, "chars": 0, "text": ""}
    status = "vision" if result.needs_vision else ("text" if result.text else "empty")
    return {
        **base,
        "status": status,
        "chars": len(result.text),
        "text": result.text,
        "pages": result.pages,
        "ms": int((clock.perf_counter() - started) * 1000),
    }


def scan_dates(text: str, anchor: date) -> Counter:
    tally: Counter = Counter()
    for match in _DATE_CANDIDATE.finditer(text):
        phrase = match.group().strip()
        if re.fullmatch(r"[\d.\s]+", phrase) and phrase.count(".") == 1:
            tally["소수처럼_보여서_뺌"] += 1  # 3.5 같은 숫자
            continue
        tally["후보"] += 1
        when = parse_notice_when(phrase, anchor=anchor)
        if when.review:
            tally[f"확인필요:{when.review.split(':')[0]}"] += 1
        elif when.start is None:
            tally["못읽음"] += 1
        else:
            tally["읽음"] += 1
            if not re.search(r"\d{2,4}\s*[년./-]\s*\d{1,2}\s*(?:월|[./-])", phrase):
                tally["연도없음"] += 1
            if re.search(r"[월화수목금토일](?:요일)?[)）]", phrase):
                tally["요일있음"] += 1
    return tally


async def fixture_child() -> tuple[uuid.UUID, uuid.UUID]:
    """실행마다 새 아이다. 같은 아이면 앞 실행의 행과 겹쳐 항목이 안 들어간 채 "중복" 으로 센다."""
    async with async_session_factory() as session:
        parent = Parent(nickname="말뭉치")
        session.add(parent)
        await session.flush()
        child = Child(nickname="말뭉치", owner_parent_id=parent.id, birth_date=date(2022, 3, 1))
        session.add(child)
        await session.commit()
        return parent.id, child.id


def _bare_notice(row: dict, doc: Doc, received: datetime) -> NewNotice:
    fmt = {
        ".pdf": NoticeInputFormat.PDF,
        ".hwp": NoticeInputFormat.HWP,
        ".hwpx": NoticeInputFormat.HWPX,
        ".xlsx": NoticeInputFormat.XLSX,
    }.get(doc.ext, NoticeInputFormat.TEXT)
    return NewNotice(
        doc_kind=NoticeDocKind.OTHER,
        input_format=fmt,
        audience_scope=NoticeAudience.UNKNOWN,
        issued_on=doc.posted_on,
        received_at=received,
        title=doc.title or None,
        raw_text=mask_names(row["text"], people=[], institutions=[]),
        content_hash=content_hash(row["text"]),
        is_correction=False,
        items=(),
    )


async def run_free(corpus: Path, out: Path) -> None:
    parent_id, child_id = await fixture_child()
    received = datetime.now(UTC)
    status: Counter = Counter()
    families: Counter = Counter()
    by_ext: Counter = Counter()
    reasons: Counter = Counter()
    dates: Counter = Counter()
    db: Counter = Counter()
    db_errors: Counter = Counter()
    with (out / "free_docs.jsonl").open("w", encoding="utf-8") as sink:
        for doc in iter_docs(corpus):
            row = read_doc(doc)
            status[row["status"]] += 1
            families[doc.family] += 1
            by_ext[f"{row['ext'] or '(없음)'}:{row['status']}"] += 1
            if reason := row.get("reason"):
                reasons[reason] += 1
            if row["status"] == "text":
                dates.update(scan_dates(row["text"], doc.posted_on or received.date()))
                async with async_session_factory() as session:
                    try:
                        saved = await save_notice(
                            session,
                            child_id=child_id,
                            uploader_parent_id=parent_id,
                            notice=_bare_notice(row, doc, received),
                        )
                        await session.commit()
                        db["새로 저장" if saved.created else "중복이라 건너뜀"] += 1
                    except Exception as e:  # 어떤 값이 DB 를 깨는지 세는 것이 목적이다
                        await session.rollback()
                        db["실패"] += 1
                        db_errors[f"{type(e).__name__}: {str(e).splitlines()[0][:120]}"] += 1
            sink.write(
                json.dumps({k: v for k, v in row.items() if k != "text"}, ensure_ascii=False)
            )
            sink.write("\n")
    summary = {
        "가족별_문서": dict(families),
        "읽기_상태": dict(status),
        "확장자별": dict(sorted(by_ext.items())),
        "못읽은_이유": dict(reasons),
        "날짜": dict(dates),
        "DB": dict(db),
        "DB_오류": dict(db_errors),
    }
    (out / "free_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _sample(docs: list[tuple[Doc, dict]], limit: int, seed: int) -> list[tuple[Doc, dict]]:
    """형식 · 주제가 고르게 돌아가며 뽑는다. 한 게시글에서는 문서 하나만."""
    rng = random.Random(seed)
    rng.shuffle(docs)
    seen: set[str] = set()
    buckets: dict[str, list[tuple[Doc, dict]]] = {}
    for doc, row in docs:
        if doc.post in seen:
            continue
        seen.add(doc.post)
        buckets.setdefault(f"{doc.ext}|{doc.topic}", []).append((doc, row))
    picked: list[tuple[Doc, dict]] = []
    while len(picked) < limit and any(buckets.values()):
        for key in list(buckets):
            if buckets[key] and len(picked) < limit:
                picked.append(buckets[key].pop())
    return picked


def _extract(extractor: NoticeExtractor, doc: Doc, row: dict) -> tuple[NoticeExtract, str, str]:
    """(결과, 근거 대조에 쓸 글자, 입력 형식)."""
    if row["status"] == "text":
        return extractor.read_text(row["text"]), row["text"], _format_of(doc)
    if doc.ext == ".pdf":
        png = pdf_pages_as_png(doc.path.read_bytes(), max_pages=1)[0]
        result = extractor.read_image(png, mime_type="image/png")
        return result, clean_text(result.transcript), "pdf"
    data, mime = normalize_image(doc.path.read_bytes())
    result = extractor.read_image(data, mime_type=mime)
    return result, clean_text(result.transcript), "image"


def _format_of(doc: Doc) -> str:
    return {".pdf": "pdf", ".hwp": "hwp", ".hwpx": "hwpx", ".xlsx": "xlsx"}.get(doc.ext, "text")


def _received(doc: Doc) -> datetime:
    """앱에서는 받은 날 올린다. 옛 공지를 오늘 받은 것으로 치면 연도 · 요일이 어긋난다."""
    if doc.posted_on is None:
        return datetime.now(UTC)
    return datetime(doc.posted_on.year, doc.posted_on.month, doc.posted_on.day, 9, tzinfo=KST)


def _worker(extractor: NoticeExtractor, pair: tuple[Doc, dict]) -> dict:
    doc, row = pair
    started = clock.perf_counter()
    base = {
        "post": doc.post,
        "file": doc.file,
        "ext": doc.ext,
        "topic": doc.topic,
        "family": doc.family,
        "received": _received(doc),
    }
    try:
        extract, source, fmt = _extract(extractor, doc, row)
    except Exception as e:  # 모델 · 게이트웨이 실패를 세는 것이 목적이다
        return {**base, "stage": "extract", "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {
        **base,
        "extract": extract,
        "source": source,
        "fmt": fmt,
        "seconds": round(clock.perf_counter() - started, 1),
    }


async def run_llm(
    corpus: Path,
    out: Path,
    *,
    max_calls: int,
    seed: int,
    workers: int,
    only: Path | None,
    family: str | None,
) -> None:
    parent_id, child_id = await fixture_child()
    extractor = NoticeExtractor.from_settings(settings, max_calls=max_calls)
    candidates: list[tuple[Doc, dict]] = []
    for doc in iter_docs(corpus):
        if family is not None and doc.family != family:
            continue
        row = read_doc(doc)
        if row["status"] in ("text", "vision") and (
            row["status"] == "vision" or row["chars"] < 40_000
        ):
            candidates.append((doc, row))
    picked = _sample(candidates, max_calls, seed)
    if only is not None:
        wanted = {(g["post"], g["file"]) for g in map(json.loads, only.open(encoding="utf-8"))}
        picked = [(d, r) for d, r in candidates if (d.post, d.file) in wanted][:max_calls]
    print(f"후보 {len(candidates)}건 중 {len(picked)}건 호출", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda pair: _worker(extractor, pair), picked))

    tally: Counter = Counter()
    items: Counter = Counter()
    reviews: Counter = Counter()
    errors: Counter = Counter()
    drafts: Counter = Counter()
    doc_kinds: Counter = Counter()
    labels: Counter = Counter()
    seconds: list[float] = []
    with (out / "llm_results.jsonl").open("w", encoding="utf-8") as sink:
        for result in results:
            extract = result.pop("extract", None)
            if extract is None:
                tally["추출 실패"] += 1
                errors[result["error"][:100]] += 1
                sink.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
                continue
            seconds.append(result["seconds"])
            doc_kinds[extract.doc_kind] += 1
            try:
                notice = assemble(
                    extract,
                    source_text=result["source"],
                    input_format=NoticeInputFormat(result["fmt"]),
                    received_at=result["received"],
                    tz=KST,
                )
            except Exception as e:  # 조립 규칙이 실제 값에서 터지는지 본다
                tally["조립 실패"] += 1
                errors[f"assemble {type(e).__name__}: {str(e)[:120]}"] += 1
                continue
            async with async_session_factory() as session:
                try:
                    saved = await save_notice(
                        session, child_id=child_id, uploader_parent_id=parent_id, notice=notice
                    )
                    await session.commit()
                    tally["저장" if saved.created else "중복"] += 1
                except Exception as e:  # 어떤 값이 DB 를 깨는지 세는 것이 목적이다
                    await session.rollback()
                    tally["DB 실패"] += 1
                    errors[f"db {type(e).__name__}: {str(e).splitlines()[0][:160]}"] += 1
            for item in notice.items:
                items[item.handling.value] += 1
                labels[item.label] += 1
                if item.needs_review:
                    items["needs_review"] += 1
                    for part in (item.review_reason or "").split(" · "):
                        reviews[part.split(":")[0]] += 1
                try:
                    draft = to_event_draft(item)
                except ValidationError as e:
                    drafts["초안 계약 위반"] += 1
                    errors[f"draft {e.errors()[0]['msg'][:100]}"] += 1
                    continue
                if draft is None:
                    if item.handling.value == "calendar":
                        drafts["날짜 없어 초안 없음"] += 1
                    continue
                try:
                    SubmitEventRequest.model_validate(
                        {
                            "event": draft.event.model_dump(mode="json"),
                            "items": [i.model_dump() for i in draft.items],
                        }
                    )
                    drafts["제출 가능"] += 1
                except ValidationError as e:
                    drafts["제출 계약 위반"] += 1
                    errors[f"submit {e.errors()[0]['msg'][:100]}"] += 1
            sink.write(
                json.dumps(
                    {
                        **{k: v for k, v in result.items() if k != "source"},
                        "source_chars": len(result["source"]),
                        "extract": extract.model_dump(),
                        "items": [asdict(i) for i in notice.items],
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )
    prompt = sum(u.prompt_tokens for u in extractor.usage)
    completion = sum(u.completion_tokens for u in extractor.usage)
    summary = {
        "호출": extractor.calls,
        "토큰": {"입력": prompt, "출력": completion},
        "결과": dict(tally),
        "문서종류": dict(doc_kinds),
        "항목": dict(items),
        "확인필요_이유": dict(reviews.most_common(20)),
        "일정초안": dict(drafts),
        "오류": dict(errors.most_common(20)),
        "라벨_상위": dict(labels.most_common(30)),
        "건당_초": {
            "중앙값": sorted(seconds)[len(seconds) // 2] if seconds else None,
            "최대": max(seconds) if seconds else None,
        },
    }
    (out / "llm_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["free", "llm"])
    parser.add_argument("corpus", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--max-calls", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--family", help="말뭉치 폴더 하나만 돌린다")
    parser.add_argument(
        "--only", type=Path, help="post · file 이 적힌 JSONL. 그 문서만 다시 돌린다"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        if args.stage == "free":
            await run_free(args.corpus, args.out)
        else:
            await run_llm(
                args.corpus,
                args.out,
                max_calls=args.max_calls,
                seed=args.seed,
                workers=args.workers,
                only=args.only,
                family=args.family,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
