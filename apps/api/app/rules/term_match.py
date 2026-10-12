"""알레르기·위험 용어를 문자열에서 찾는 공통 매처.

Food 알레르기 필터와 Activity 위험 용어가 같은 규칙을 쓴다(공통_구현_계획 §4-1).
순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import unicodedata
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
    """글자 비교용. NFKC 로 맞추고 영문은 소문자로, 공백 · 문장부호 · 기호는 지운다.

    NFKC는 자모로 풀린 한글(NFD)과 전각 영문("ｍｉｌｋ")을 보통 글자로 되돌린다. 안 하면
    눈에는 같은 "우유" 가 별칭과 맞지 않는다. 문장부호 · 기호 · 보이지 않는 서식 문자도
    지운다 — "pine-nut" · "'우유'" 도 "pine nut" · "우유" 와 같은 이름이다. 메뉴를 훑는
    `match_fields` 와 같은 글자만 남긴다(`_compact`).
    """
    return _compact(text)[0]


def split_words(text: str) -> list[str]:
    """`normalize` 한 낱말들. 공백 · 문장부호 · 기호가 있던 자리(`_compact` 의 경계)에서 나눈다."""
    compact, breaks, _ = _compact(text)
    edges = [0, *sorted(breaks), len(compact)]
    return [compact[start:end] for start, end in zip(edges, edges[1:]) if start < end]


def _invisible(ch: str) -> bool:
    """폭 없는 공백 · soft hyphen 같은 서식 문자(Cf). 복사해 온 글에 섞여 와도 눈에 안 보인다."""
    return unicodedata.category(ch) == "Cf"


def _is_break(ch: str) -> bool:
    """단어 경계가 되는 글자 — 공백 · 문장부호 · 기호 · 제어 문자."""
    return ch.isspace() or unicodedata.category(ch)[0] in "PSZC"


def match_terms(text: str, terms: Sequence[Term]) -> tuple[str, ...]:
    """text 에서 걸린 Term.key 를 terms 순서대로 돌려준다.

    alias 가 나온 자리마다 guard 가 그 자리를 덮는지 본다. 덮이지 않은 자리가
    하나라도 있으면 건다 — 애매하면 거는 쪽이 이긴다(가장 엄한 판정 우선).
    예: "밀크티와 통밀빵" — 앞의 "밀"은 guard "밀크"가 덮어 취소되지만, 뒤의
    "통밀"의 "밀"은 어떤 guard 도 덮지 않으므로 걸린다.
    글 전체를 `normalize` 해서 보므로 별칭 · guard 가 공백 · 문장부호를 넘는다. 칸과 경계를
    지켜야 하면 `match_fields` 를 쓴다.
    """
    normalized = normalize(text)
    if not normalized:
        return ()
    hits: list[str] = []
    for term in terms:
        if _term_hits(normalized, term):
            hits.append(term.key)
    return tuple(hits)


def match_fields(fields: Sequence[str], terms: Sequence[Term]) -> tuple[str, ...]:
    """각 필드(메뉴명·재료)를 독립적으로 검사해 매칭된 `Term.key`를 `terms` 순서대로 반환한다.

    `match_terms`와 달리 필드 경계와 문장부호를 고려해 guard가 실제 재료명을 과도하게 가리는 것을
    방지한다.

    - 필드를 이어 붙이지 않는다. 따라서 서로 다른 필드에 걸친 alias/guard는 매칭하지 않는다.
    - guard는 띄어쓰기·문장부호를 넘지 않는다.
      단, guard 자체에 띄어쓰기가 있으면 해당 위치는 허용한다.
    - alias는 띄어쓰기는 넘어 매칭하지만 문장부호는 넘지 않는다.
      단, alias 자체에 띄어쓰기가 있으면 해당 위치의 문장부호도 허용한다.
    """

    return TermMatcher(terms).match(prepare_fields(fields))


# `prepare_fields` 결과 — 필드마다 (공백 · 문장부호를 지운 글자열, 지운 자리, 그중 문장부호 자리)
PreparedFields = tuple[tuple[str, frozenset[int], frozenset[int]], ...]


def prepare_fields(fields: Sequence[str]) -> PreparedFields:
    """`TermMatcher.match` 에 넘길 형태로 한 번만 바꿔 둔다."""
    return tuple(found for found in map(_compact, fields) if found[0])


class TermMatcher:
    """같은 Term 묶음으로 여러 행을 훑을 때 쓴다. 판정은 `match_fields` 와 같다.

    별칭 색인을 한 번 만들어 두고, 행마다 글자에 실제로 나온 별칭의 Term 만 guard 까지 본다.
    후보 풀은 카탈로그 전체라 행이 수천 개가 될 수 있다.
    """

    def __init__(self, terms: Sequence[Term]) -> None:
        self._terms = tuple(terms)
        self._compiled = tuple(_compiled(term) for term in self._terms)
        index: dict[str, set[int]] = {}
        for position, (aliases, _) in enumerate(self._compiled):
            for word, _ in aliases:
                index.setdefault(word, set()).add(position)
        self._index = {word: frozenset(found) for word, found in index.items()}
        self._lengths = sorted({len(word) for word in index})

    def match(self, prepared: PreparedFields) -> tuple[str, ...]:
        """걸린 Term.key 를 terms 순서대로."""
        candidates: set[int] = set()
        for text, _, _ in prepared:
            size = len(text)
            for start in range(size):
                for length in self._lengths:
                    if start + length > size:
                        break
                    found = self._index.get(text[start : start + length])
                    if found:
                        candidates |= found
        return tuple(
            self._terms[position].key
            for position in sorted(candidates)
            if any(
                _field_hits(text, breaks, hard, *self._compiled[position])
                for text, breaks, hard in prepared
            )
        )


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


def _field_hits(
    text: str,
    breaks: frozenset[int],
    hard: frozenset[int],
    aliases: tuple[tuple[str, frozenset[int]], ...],
    guards: tuple[tuple[str, frozenset[int]], ...],
) -> bool:
    guard_spans: list[tuple[int, int]] | None = None
    for word, inner in aliases:
        if word not in text:
            continue
        if guard_spans is None:  # guard 자리는 별칭이 나올 때만 센다
            guard_spans = [
                (start, start + len(guard))
                for guard, inner in guards
                for start in _find_all(text, guard)
                if _breaks_inside(breaks, start, start + len(guard)) <= inner
            ]
        for start in _find_all(text, word):
            end = start + len(word)
            if not _breaks_inside(hard, start, end) <= inner:
                continue  # 문장부호를 넘는 자리(다른 재료에 걸친 글자다)
            if not _covered_by_any(start, end, guard_spans):
                return True
    return False


_Compiled = tuple[tuple[str, frozenset[int]], ...]


def _compiled(term: Term) -> tuple[_Compiled, _Compiled]:
    """`match_fields` 가 쓰는 별칭 · guard 의 압축형 — (글자, 그 글자 안의 단어 경계)."""
    aliases = tuple((word, inner) for word, inner, _ in map(_compact, term.aliases) if word)
    guards = tuple((word, inner) for word, inner, _ in map(_compact, term.guards) if word)
    return aliases, guards


def _compact(text: str) -> tuple[str, frozenset[int], frozenset[int]]:
    """NFKC · 소문자로 맞추고 공백 · 문장부호 · 기호를 지운 글자열, 지운 자리(단어 경계),
    그중 문장부호 · 기호가 있던 자리. 보이지 않는 서식 문자는 경계 없이 지운다
    (`normalize` 와 동일)."""
    chars: list[str] = []
    breaks: set[int] = set()
    hard: set[int] = set()
    for ch in unicodedata.normalize("NFKC", text).lower():
        if _invisible(ch):
            continue
        if _is_break(ch):
            if chars:
                breaks.add(len(chars))
                if not ch.isspace():
                    hard.add(len(chars))
            continue
        chars.append(ch)
    return "".join(chars), frozenset(breaks), frozenset(hard)


def _breaks_inside(breaks: frozenset[int], start: int, end: int) -> frozenset[int]:
    """[start, end) 안쪽의 단어 경계를 start 기준 위치로."""
    return frozenset(b - start for b in breaks if start < b < end)


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
