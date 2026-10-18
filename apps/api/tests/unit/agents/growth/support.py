"""Growth 테스트가 같이 쓰는 값과 만드는 함수. 테스트 파일이 아니다(수집되지 않는다)."""

import json
from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.common.age import stage_of
from app.agents.common.gate import DataReady, Gate
from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.growth.context import GrowthContext
from app.agents.growth.store.inmemory import in_memory_ports
from app.agents.growth.store.ports import GrowthMeasurement, UpstreamUnavailable
from app.core.book_raw import RawBook
from app.rules.age import LifeStage

CHILD = UUID(int=1)
KST = ZoneInfo("Asia/Seoul")
# KST 9/28 01:00 = UTC 9/27 16:00. UTC 날짜로 세면 하루가 어긋나는 시간대다
NOW = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
TODAY = date(2026, 9, 28)
# KST 로는 12개월 당일, UTC 로는 11개월
BIRTH_12_IN_KST = date(2025, 9, 28)
BIRTH_48 = date(2022, 9, 28)

REPO_ROOT = Path(__file__).resolve().parents[6]
GROWTH_DOCS = REPO_ROOT / "docs" / "agents" / "growth"


def birth_for(months: int, today: date = TODAY) -> date:
    """오늘 기준으로 정확히 `months` 개월인 아이의 생일(일은 오늘과 같다)."""
    total = today.year * 12 + (today.month - 1) - months
    return date(total // 12, total % 12 + 1, today.day)


def gate(months: int, **kwargs) -> Gate:
    """registry 테스트용 Gate. 기본은 동의 있음 · 안전 조회 성공 · 공지 없음 · 도서 API 정상."""
    stage = LifeStage(
        months=months, stage=stage_of(months), big="infant" if months < 12 else "toddler"
    )
    base = dict(stage=stage, consent_child_health=True, safety_ok=True, data=DataReady())
    return Gate(**{**base, **kwargs})


def context(birth: date | None = None, *, consent: bool = True, **ports) -> GrowthContext:
    return GrowthContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(CHILD, birth or BIRTH_48, consent=consent, **ports),
    )


def task(label: str | None, text: str = "테스트 요청") -> DomainTask:
    return DomainTask(run_id="run-1", agent="growth", task_type=label, request_texts=(text,))


def measurement(
    n: int, day: date, height: str | None = None, weight: str | None = None
) -> GrowthMeasurement:
    return GrowthMeasurement(
        id=UUID(int=1000 + n),
        child_id=CHILD,
        measured_on=day,
        height_cm=Decimal(height) if height is not None else None,
        weight_kg=Decimal(weight) if weight is not None else None,
    )


def doc_inputs() -> dict[str, str]:
    """`test_input_growth.txt` 의 [Txx] 입력. 문서가 정본이다."""
    lines = (GROWTH_DOCS / "test_input_growth.txt").read_text(encoding="utf-8").splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        if line.startswith("[T") and "] " in line:
            key, text = line[1:].split("] ", 1)
            parsed[key] = text
    return parsed


class ProposesExamples:
    """[예시] 첫 문서 행 id 를 system 프롬프트에서 읽어 교육 후보 셋을 내는 모델 가짜.

    [예시]는 아이 · 날짜로 돌려 고르므로 테스트가 id 를 미리 알 수 없다. 받은 대화는 `seen` 에
    남긴다 — 모델 입력에 건강정보가 없는지 테스트가 본다.
    """

    TITLES = ("블록 세기", "색깔 찾기", "모양 맞추기")

    def __init__(self, titles: tuple[str, ...] = TITLES) -> None:
        self.titles = titles
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.seen.append([dict(m) for m in messages])
        doc_id = messages[0]["content"].split("- id ", 1)[1].split(" ", 1)[0]
        items = [
            {
                "title": title,
                "steps": ["하나씩 해 본다"],
                "materials": [],
                "evidence": [{"id": doc_id, "note": "이 나이 예시를 참고했어요"}],
                "reason": "이 나이에 많이 하는 활동이에요",
            }
            for title in self.titles
        ]
        call = SimpleNamespace(
            id=f"p{len(self.seen)}",
            function=SimpleNamespace(
                name="propose_learning_activity",
                arguments=json.dumps({"items": items}, ensure_ascii=False),
            ),
        )
        return LLMResponse(
            message=SimpleNamespace(content=None, tool_calls=[call]), usage={}, latency_ms=1
        )


def raw_book(
    n: int,
    *,
    title: str | None = None,
    symbol: str | None = "77810",
    class_no: str | None = "813.8",
    authors: str | None = "글: 가나다 ; 그림: 라마바",
    isbn: str | None = None,
) -> RawBook:
    """정보나루 원문 한 권 — 가짜 값이다. 기본은 아동(부가기호 7) 문학(KDC 8) = 그림책."""
    return RawBook(
        isbn13=isbn if isbn is not None else f"978890000{n:04d}",
        title=title if title is not None else f"책{n}",
        authors=authors,
        publisher="출판사",
        addition_symbol=symbol,
        class_no=class_no,
    )


class FakeRawSearch:
    """`RawBookSearch` 가짜. 검색어마다 정해 둔 원문을 주고 `fail` 의 검색어는 조회 실패다."""

    def __init__(
        self, by_keyword: dict[str, Sequence[RawBook]], *, fail: Collection[str] = ()
    ) -> None:
        self._by_keyword = {keyword: tuple(rows) for keyword, rows in by_keyword.items()}
        self._fail = frozenset(fail)
        self.calls: list[tuple[str, int]] = []

    async def search(self, *, keyword: str, limit: int) -> tuple[RawBook, ...]:
        self.calls.append((keyword, limit))
        if keyword in self._fail:
            raise UpstreamUnavailable("정보나루 조회 실패 (테스트 주입)")
        return self._by_keyword.get(keyword, ())[:limit]
