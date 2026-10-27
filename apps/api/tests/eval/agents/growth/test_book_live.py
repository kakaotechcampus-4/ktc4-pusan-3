"""Growth 도서 라이브 드라이런 — 실제 정보나루 응답으로 연령 필터가 얼마나 거르는지, 실제 모델로
run() 이 검색한 책만 내는지 본다.

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/growth/test_book_live.py -m live -s

키: DATA4LIBRARY_AUTH_KEY(정보나루) · 모델 케이스는 GROWTH_*. 검색어 캐시는 쓰지 않는다
(cache=None) — 늘 실제 응답을 본다. 호출은 필터 표 10번 + 모델 케이스마다 1~3번이라 하루 500회
안이다.
결과 파일(test_book_live_results.jsonl, gitignore)에는 건수 · 형태 · 월령만 남긴다. 실제 사용자 글은
없다. 기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.agent import run
from app.agents.growth.context import GrowthContext
from app.agents.growth.library import LibraryBooks
from app.agents.growth.store.inmemory import (
    InMemoryGrowthMemory,
    InMemoryIssuedBooks,
    in_memory_ports,
)
from app.agents.growth.store.ports import BookRow, EducationObservation
from app.agents.growth.tools.books import MAX_BOOKS, SEARCH_POOL, book_label
from app.core.config import settings
from app.integrations.data4library.client import Data4LibraryBooks
from app.rules.book_form import fits_age
from app.rules.evaluative import find_evaluative
from app.rules.term_match import normalize

pytestmark = pytest.mark.live

CHILD = UUID(int=7)
KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 17, 10, tzinfo=KST)
TODAY = date(2026, 10, 17)
MONTHS = (8, 20, 30, 48)
KEYWORDS = ("공룡", "자동차", "동물", "잠", "목욕")
INFANT_WORD = "보드북"
RESULTS = Path(__file__).with_name("test_book_live_results.jsonl")
_pools: dict[tuple[str, ...], list[BookRow]] = {}


def born(months: int) -> date:
    return TODAY - timedelta(days=int(months * 30.44))


def _write(record: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module")
def auth_key() -> str:
    key = settings.DATA4LIBRARY_AUTH_KEY.strip()
    if not key:
        pytest.skip("DATA4LIBRARY_AUTH_KEY 가 없다 — apps/api/.env 에 넣고 돌린다")
    return key


@pytest.fixture(scope="module")
def llm() -> LLMClient:
    try:
        return LLMClient(role="growth")
    except LLMConfigError:
        pytest.skip("GROWTH_* 모델 설정이 필요하다 — apps/api/.env 를 확인한다")


def library(key: str) -> LibraryBooks:
    return LibraryBooks(Data4LibraryBooks(auth_key=key, cache=None))


async def pool(key: str, keywords: tuple[str, ...]) -> list[BookRow]:
    if keywords not in _pools:
        _pools[keywords] = await library(key).search(keywords=keywords, limit=SEARCH_POOL)
    return _pools[keywords]


async def test_연령_필터_표(auth_key):
    """검색어마다 50권에서 월령별로 몇 권이 남는가.

    8개월은 "<검색어> 보드북" 을 같이 찾은 값도 잰다.
    """
    table: dict[str, dict[str, Any]] = {}
    for keyword in KEYWORDS:
        rows = await pool(auth_key, (keyword,))
        boosted = await pool(auth_key, (keyword, f"{keyword} {INFANT_WORD}"))
        assert len(rows) <= SEARCH_POOL
        table[keyword] = {
            "rows": len(rows),
            "forms": dict(Counter(row.form for row in rows)),
            "fit": {months: sum(fits_age(r.form, months) for r in rows) for months in MONTHS},
            "fit_8_boosted": sum(fits_age(r.form, 8) for r in boosted),
        }
        _write({"case": "filter", "keyword": keyword, **table[keyword]})
    print(json.dumps(table, ensure_ascii=False, indent=1))

    # 30 · 48개월은 검색어 대부분에서 3권이 차야 한다 — 안 차면 필터가 실제 응답을 너무 많이 거른다
    for months in (30, 48):
        filled = sum(1 for keyword in KEYWORDS if table[keyword]["fit"][months] >= MAX_BOOKS)
        assert filled >= 4, (months, table)


async def play(llm, key, case, request, *, months, education=(), issued=()):
    ports = in_memory_ports(
        CHILD,
        born(months),
        consent=False,
        memory=InMemoryGrowthMemory(education=list(education)),
        books=library(key),
        issued_books=InMemoryIssuedBooks(issued),
    )
    context = GrowthContext(
        child_id=CHILD, run_id=f"eval-{case}", now=NOW, timezone=KST, ports=ports
    )
    task = DomainTask(
        run_id=context.run_id, agent="growth", task_type="book_suggestion", request_texts=(request,)
    )
    result = await run(task, context, client=llm)
    rows = {book_label(row): row for row in context.state.seen_books.values()}
    _write(
        {
            "case": case,
            "months": months,
            "status": result.status,
            "model_calls": result.model_calls,
            "kinds": [d.kind for d in result.suggestions],
            "forms": [rows[d.content].form for d in result.suggestions if d.content in rows],
            "notices": [r.code for r in result.readouts],
        }
    )
    return result, rows


def check(result, rows, *, months):
    assert result.status == "completed", (result.status, [r.body for r in result.readouts])
    assert len(result.suggestions) <= MAX_BOOKS
    for draft in result.suggestions:
        assert draft.content in rows  # 검색한 책만 — 지어낸 책 0건
        assert fits_age(rows[draft.content].form, months)
        assert not find_evaluative(draft.reason) and not find_evaluative(draft.content)
        books = [c for c in draft.citations if c.ref.kind == "book_catalog"]
        if draft.kind == "personalized":
            # 개인화 근거는 아이 기록뿐 — 책 근거는 일반 추천에만 붙는다
            assert any(c.ref.is_child_record for c in draft.citations) and not books
        else:
            assert len(books) == 1


async def test_8개월_기록_없음은_보드북_일반_추천(llm, auth_key):
    result, rows = await play(llm, auth_key, "infant", "아기랑 같이 볼 책 추천해줘", months=8)
    check(result, rows, months=8)
    assert {d.kind for d in result.suggestions} <= {"general"}


async def test_30개월_공룡_관심은_개인화(llm, auth_key):
    edu = EducationObservation(
        id=UUID(int=901),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=3),
        subject="공룡",
        topic="공룡 그림을 오래 봤다",
        polarity=1,
    )
    result, rows = await play(
        llm, auth_key, "dino", "공룡 나오는 책 추천해줘", months=30, education=[edu]
    )
    check(result, rows, months=30)
    assert any(d.kind == "personalized" for d in result.suggestions)


async def test_다른_책도_는_이미_낸_책을_다시_내지_않는다(llm, auth_key):
    first, _ = await play(llm, auth_key, "again-1", "자동차 책 추천해줘", months=48)
    issued = [d.content for d in first.suggestions]
    second, rows = await play(
        llm, auth_key, "again-2", "자동차 책 다른 것도 알려줘", months=48, issued=issued
    )
    check(second, rows, months=48)
    assert not {normalize(d.content) for d in second.suggestions} & {normalize(x) for x in issued}
