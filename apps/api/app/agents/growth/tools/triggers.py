"""습관 상황 태그 — 관찰의 trigger 를 habit_strategy 행의 trigger_tags 로 옮긴다 (코드).

목록은 `reference/growth_triggers.yaml` 이다 (Growth_Tool_명세.md §2 "예절 · 습관 — 전략 행").
보호자가 말한 trigger 는 자유 문장이라 문서 조회에 그대로 넣지 않는다 — 걸린 태그만 넘긴다
(RAG_plan §7 "쿼리에 보호자 원문을 넣지 않는다"). 어느 태그에도 안 걸리면 빈 튜플이고, 호출부는
전략 행을 고르지 않는다 — 언제 나오는 행동인지 모르는 교정안은 아이와 상관없는 일반론이다.
"""

import re
from collections.abc import Sequence
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.rules.term_match import Term, match_terms, normalize

TRIGGER_FILE = "growth_triggers.yaml"
# search_growth_doc 이 받는 태그 모양과 같다
_TAG = re.compile(r"[a-z][a-z0-9_]*")
_KEYS = frozenset({"tag", "aliases", "guards"})


@cache
def trigger_terms() -> tuple[Term, ...]:
    """`growth_triggers.yaml` 을 읽는다. 읽지 못하거나 모양이 틀리면 예외를 그대로 올린다."""
    return parse_trigger_terms(load_reference(TRIGGER_FILE))


def parse_trigger_terms(data: dict[str, Any]) -> tuple[Term, ...]:
    raws = data.get("triggers")
    if not isinstance(raws, list) or not raws:
        raise ValueError(f"{TRIGGER_FILE} 에 triggers 가 없다")
    terms: list[Term] = []
    for raw in raws:
        if extra := set(raw) - _KEYS:
            raise ValueError(f"{TRIGGER_FILE} 에 정해지지 않은 칸이 있다: {sorted(extra)}")
        tag = raw.get("tag")
        aliases = tuple(raw.get("aliases") or ())
        guards = tuple(raw.get("guards") or ())
        if not isinstance(tag, str) or not _TAG.fullmatch(tag):
            raise ValueError(f"{TRIGGER_FILE} 의 tag 는 영문 소문자 식별자여야 한다: {tag!r}")
        if not aliases:
            raise ValueError(f"{TRIGGER_FILE} {tag!r} 의 aliases 가 비었다")
        if any(term.key == tag for term in terms):
            raise ValueError(f"{TRIGGER_FILE} 에 tag 가 겹친다: {tag!r}")
        for alias in aliases:
            if len(normalize(alias)) < 2 and not guards:
                raise ValueError(
                    f"{TRIGGER_FILE} {tag!r} 의 한 글자 별칭 {alias!r} 에 guard 가 없다 — 과차단"
                )
        terms.append(Term(key=tag, aliases=aliases, guards=guards))
    return tuple(terms)


def trigger_tag_set() -> frozenset[str]:
    """사전에 있는 태그 전부. 적재 검사가 habit_strategy 행의 trigger_tags 와 맞춰 본다."""
    return frozenset(term.key for term in trigger_terms())


def trigger_tags(triggers: Sequence[str | None]) -> tuple[str, ...]:
    """관찰 trigger 들에서 걸린 태그. 처음 걸린 순서대로, 겹치지 않게. 비었으면 빈 튜플."""
    terms = trigger_terms()
    found: dict[str, None] = {}
    for text in triggers:
        if text and text.strip():
            found.update(dict.fromkeys(match_terms(text, terms)))
    return tuple(found)
