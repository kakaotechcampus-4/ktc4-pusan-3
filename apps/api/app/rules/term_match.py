"""알레르기·위험 용어를 문자열에서 찾는 공통 매처.

Food 알레르기 필터와 Activity 위험 용어가 같은 규칙을 쓴다(공통_구현_계획 §4-1).
순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    """매칭 대상 용어 하나.

    key      — 매칭됐을 때 돌려줄 식별자. Food 에서는 알레르기 코드("1".."19") 또는
               health_safety.label 이 된다.
    aliases  — 걸어야 할 표기들. 공백을 지우고 부분 일치로 찾는다.
    guards   — 오탐 취소 표기("밀크"는 "밀"이 아니다). alias 가 나온 자리를 guard
               문자열이 덮으면 그 자리는 걸지 않는다.
    """

    key: str
    aliases: tuple[str, ...]
    guards: tuple[str, ...] = ()


def normalize(text: str) -> str:
    """공백을 전부 지우고 영문은 소문자로."""
    return "".join(text.split()).lower()


def match_terms(text: str, terms: Sequence[Term]) -> tuple[str, ...]:
    """text 에서 걸린 Term.key 를 terms 순서대로 돌려준다.

    alias 가 나온 자리마다 guard 가 그 자리를 덮는지 본다. 덮이지 않은 자리가
    하나라도 있으면 건다 — 애매하면 거는 쪽이 이긴다(가장 엄한 판정 우선).
    예: "밀크티와 통밀빵" — 앞의 "밀"은 guard "밀크"가 덮어 취소되지만, 뒤의
    "통밀"의 "밀"은 어떤 guard 도 덮지 않으므로 걸린다.
    """
    normalized = normalize(text)
    if not normalized:
        return ()
    hits: list[str] = []
    for term in terms:
        if _term_hits(normalized, term):
            hits.append(term.key)
    return tuple(hits)


def _term_hits(normalized: str, term: Term) -> bool:
    guard_spans = _spans(normalized, term.guards)
    for alias in term.aliases:
        alias_norm = normalize(alias)
        if not alias_norm:
            continue
        for start in _find_all(normalized, alias_norm):
            end = start + len(alias_norm)
            if not _covered_by_any(start, end, guard_spans):
                return True
    return False


def _spans(haystack: str, words: Sequence[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for word in words:
        word_norm = normalize(word)
        if not word_norm:
            continue
        for start in _find_all(haystack, word_norm):
            spans.append((start, start + len(word_norm)))
    return spans


def _find_all(haystack: str, needle: str) -> list[int]:
    """겹치는 자리도 찾는다 (예: "아아아"에서 "아아"는 두 자리)."""
    positions: list[int] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


def _covered_by_any(start: int, end: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)
