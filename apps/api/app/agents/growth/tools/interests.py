"""도서 검색어의 근거 — `search_book_interests`.

도서는 "무엇을 읽힐까" 가 관심에서 나온다. 관심은 한 곳에만 있지 않다 — 확정된 관심(affinity),
최근 배운 것, 놀이, 생활 루틴에서 같은 낱말이 되풀이되기도 하고 서로 다른 낱말이 나오기도 한다.
이 tool 이 그 기록을 한 번에 읽어 **코드가 정한 점수**로 줄 세운다. 어떤 낱말로 어떻게 검색할지
(두 주제를 엮을지, 책이 모자라면 다음 낱말로 다시 찾을지)는 점수를 본 모델이 정한다.

점수 = Σ 출처 가중치 × 최근성 × 극성 계수. 같은 낱말(서로 포함하는 두 글자 이상 낱말은 한 주제)의
기록은 점수를 더한다.

    출처 가중치   확정 관심 3.0 · 쌓이는 관심 2.0 · 교육 기록 1.5 · 놀이 기록 1.0 · 생활 기록 1.0
    최근성       관찰은 1 / (1 + 경과일 / 7), 관심은 마지막 관찰일 기준 1 / (1 + 경과일 / 30)
                 — 관심은 여러 번 쌓여 굳은 값이라 하루 이틀 차이로 순위가 흔들리지 않는다
    극성 계수     긍정 1.0 · 중립 0.5. 부정(싫어함)은 교육 · 놀이 · 관심에서 뺀다.
                 생활 기록은 부정(힘들어함)만 쓴다

빼는 것
  - 6개월 지난 관심. 14일 지난 관찰은 읽지 않는다(`rank_evidence` 창).
  - 습관 카테고리 · 의료 처치 · 증상처럼 보이는 행동(`closure_key`)의 루틴 기록. 낙인이 되거나
    Growth 가 코드로 닫는 주제로 책을 고르지 않는다.
  - 음식 기록. Growth 의 읽기 표에 없고 읽는 포트도 없다.
  - 12개월 전 교육 기록. 교육은 12개월부터 열린다.

생활 루틴은 **힘들어하는 행동(극성 −1, 예: 양치 거부)만** 주제가 된다 — 이야기책이 도울 수 있다.
도움 수준만 적은 중립 기록은 책이 필요하다는 신호가 아니다("양치하기 기록이 있으니 양치 책" 으로
요청과 상관없는 책을 고르게 된다). 그 기록을 근거로 달면 `build()` 가 reason 에 그 행동 이름이
있는지 본다(기피 근거 규칙).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final

from app.agents.common.evidence import (
    OBSERVATION_WINDOW_DAYS,
    ObservationRow,
    RankedEvidence,
    rank_evidence,
)
from app.agents.growth.context import GrowthContext
from app.agents.growth.gating import EDUCATION_MIN_MONTH
from app.agents.growth.result import ErrorCode, ToolResult, fail, ok
from app.agents.growth.schemas.memory import SearchBookInterestsArgs
from app.agents.growth.tools.closures import closure_key
from app.rules.staleness import is_stale
from app.rules.term_match import normalize

MAX_THEMES = 5
MAX_EVIDENCE_PER_THEME = 3
# 서로 포함하는 두 낱말을 한 주제로 볼 때 짧은 쪽의 최소 길이 ("물" ≠ "물감")
MIN_MERGE_LENGTH = 2
# 책이 도울 수 있는 생활 주제. 습관은 교정 대상이라 책 주제로 삼지 않는다
ROUTINE_THEME_CATEGORIES: Final = frozenset(
    {"self_care", "mealtime", "household_task", "transition", "social_manner"}
)

SOURCE_WEIGHT = MappingProxyType(
    {
        "affinity_confirmed": 3.0,
        "affinity_candidate": 2.0,
        "observation_education": 1.5,
        "observation_activity": 1.0,
        "observation_routine": 1.0,
    }
)
SOURCE_LABEL = MappingProxyType(
    {
        "affinity_confirmed": "확정 관심",
        "affinity_candidate": "쌓이는 관심",
        "observation_education": "교육 기록",
        "observation_activity": "놀이 기록",
        "observation_routine": "생활 기록",
    }
)
OBSERVATION_RECENCY_DAYS = 7
AFFINITY_RECENCY_DAYS = 30
NEUTRAL_FACTOR = 0.5

_NO_INTERESTS = (
    "관심 기록이 없다. 요청에 나온 주제 낱말로 search_books 를 부른다 — "
    "요청에도 주제가 없으면 넓은 낱말(예: 아기 책 · 그림책 · 보드북)로 찾는다."
)


@dataclass(frozen=True)
class Theme:
    keyword: str
    score: float
    sources: tuple[str, ...]  # SOURCE_LABEL, 가중치 큰 순서
    evidence: tuple[RankedEvidence, ...]  # 점수 기여가 큰 순서, MAX_EVIDENCE_PER_THEME 까지


@dataclass(frozen=True)
class _Scored:
    item: RankedEvidence
    source: str
    value: float


def _source(item: RankedEvidence) -> str:
    if item.ref.kind == "profile_affinity":
        return "affinity_confirmed" if item.tier == 1 else "affinity_candidate"
    return item.ref.kind


def _score(item: RankedEvidence, source: str, today: date) -> float | None:
    """한 기록의 점수. 주제로 쓰지 않는 기록이면 None."""
    if is_stale(item.observed_on, today):
        return None
    if source == "observation_routine":
        if item.polarity >= 0:
            return None  # 힘들어하는 행동만 — 중립 · 긍정 기록은 책 주제가 아니다
    elif item.polarity < 0:
        return None
    days = max(0, (today - item.observed_on).days)
    scale = AFFINITY_RECENCY_DAYS if source.startswith("affinity") else OBSERVATION_RECENCY_DAYS
    factor = NEUTRAL_FACTOR if item.polarity == 0 else 1.0
    return SOURCE_WEIGHT[source] * factor / (1 + days / scale)


def search_term(subject: str) -> str:
    """검색어로 쓸 낱말. 행동 이름의 "~하기" 는 뗀다 — "양치하기" 는 정보나루에서 책이 0권이고
    "양치" 는 그림책이 나온다. 떼고 두 글자 미만이 되면(말하기) 그대로 둔다."""
    text = " ".join(subject.split())
    if text.endswith("하기") and len(text) - 2 >= MIN_MERGE_LENGTH:
        return text[:-2].rstrip()
    return text


def _related(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_MERGE_LENGTH and short in long


def score_themes(ranked: Sequence[RankedEvidence], *, today: date) -> tuple[Theme, ...]:
    """근거를 주제로 묶고 점수 순으로 `MAX_THEMES` 개까지."""
    scored: list[_Scored] = []
    for item in ranked:
        source = _source(item)
        value = _score(item, source, today)
        if value is not None and normalize(item.subject):
            scored.append(_Scored(item, source, value))
    # 점수 큰 기록부터 주제를 만든다 — 같은 입력이면 늘 같은 묶음이 나온다
    scored.sort(key=lambda s: (-s.value, normalize(s.item.subject)))

    groups: list[list[_Scored]] = []
    for entry in scored:
        key = normalize(entry.item.subject)
        for group in groups:
            if any(_related(key, normalize(member.item.subject)) for member in group):
                group.append(entry)
                break
        else:
            groups.append([entry])

    themes = [
        Theme(
            keyword=search_term(
                min((m.item.subject for m in group), key=lambda text: len(normalize(text)))
            ),
            score=round(sum(m.value for m in group), 2),
            sources=tuple(
                SOURCE_LABEL[source]
                for source in sorted({m.source for m in group}, key=lambda s: -SOURCE_WEIGHT[s])
            ),
            evidence=tuple(m.item for m in group[:MAX_EVIDENCE_PER_THEME]),
        )
        for group in groups
    ]
    themes.sort(key=lambda t: (-t.score, normalize(t.keyword)))
    return tuple(themes[:MAX_THEMES])


async def search_book_interests(
    context: GrowthContext, args: SearchBookInterestsArgs
) -> ToolResult:
    """교육 · 관심 · 놀이 · 생활 루틴 기록을 한 번에 읽어 점수 순 주제로 돌려준다."""
    gate = context.state.gate
    if gate is None:
        raise RuntimeError("Gate 없이 기억 조회가 불렸다 — run() 이 build_gate 를 먼저 부른다")
    months = gate.stage.months
    today = context.today
    start = today - timedelta(days=OBSERVATION_WINDOW_DAYS)
    memory = context.ports.memory
    child_id = context.child_id

    education_open = months >= EDUCATION_MIN_MONTH
    domains = ("education", "activity") if education_open else ("activity",)
    affinities = await memory.affinities(child_id=child_id, domains=domains)
    education = (
        await memory.education(child_id=child_id, date_from=start, date_to=today)
        if education_open
        else []
    )
    activities = await memory.activities(child_id=child_id, date_from=start, date_to=today)
    routine = await memory.routine(child_id=child_id, date_from=start, date_to=today)

    rows = [
        *(
            ObservationRow(
                id=r.id,
                kind="observation_education",
                subject=r.subject,
                label=r.topic,
                polarity=r.polarity,
                observed_on=r.observed_on,
            )
            for r in education
        ),
        *(
            ObservationRow(
                id=r.id,
                kind="observation_activity",
                subject=r.subject,
                label=r.activity,
                polarity=r.polarity,
                observed_on=r.observed_on,
            )
            for r in activities
        ),
        *(
            ObservationRow(
                id=r.id,
                kind="observation_routine",
                subject=r.subject,
                polarity=r.polarity,
                observed_on=r.observed_on,
            )
            for r in routine
            if r.routine_category in ROUTINE_THEME_CATEGORIES and closure_key((r.subject,)) is None
        ),
    ]
    themes = score_themes(rank_evidence(tuple(affinities), tuple(rows), today=today), today=today)
    if not themes:
        return fail("query", "search_book_interests", ErrorCode.NO_RECORDS, _NO_INTERESTS)

    for theme in themes:
        context.state.seen_evidence.update({e.ref.id: e for e in theme.evidence})
    return ok(
        "query",
        "search_book_interests",
        themes=[
            {
                "keyword": theme.keyword,
                "score": theme.score,
                "signals": list(theme.sources),
                "evidence": [
                    {
                        "id": str(e.ref.id),
                        "kind": e.ref.kind,
                        "tier": e.tier,
                        "label": e.label,
                        "polarity": e.polarity,
                        "observed_on": e.observed_on.isoformat(),
                    }
                    for e in theme.evidence
                ],
            }
            for theme in themes
        ],
    )
