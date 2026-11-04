"""Growth 문장 가드 — 모델이 쓴 문장의 진단명 · 강압 · 구매 유도를 사람이 정한 목록으로 막는다.

목록은 `reference/growth_guard.yaml` 이다 (Growth_Tool_명세.md §2 "안전 필터").

    diagnosis  진단명 · 병명 · 치료    Growth 는 진단하지 않는다 (루트 §2)
    coercion   체벌 · 가두기 · 굶기기 · 겁주기
    commerce   구매 · 결제 · 구독 · 앱 설치     상품 추천을 만들지 않는다 (루트 §1)

기존 안전 필터(음식 · 알레르기 · 위험 용어)와 평가 표현 검사 다음에 한 번 더 보는 자리다.
걸린 후보는 고치지 않고 뺀다 — 출력 검증(`review.py`)이 재호출 제외 목록으로 보낸다.
어느 낱말에 걸렸는지는 싣지 않고 종류 키만 돌려준다(로그 · 모델 입력으로 흘러가지 않게).
칸은 **하나씩 따로** 대조하고 띄어쓰기를 넘지 않는다 — 제목 끝과 단계 첫머리가 이어져 걸리지
않게(`match_fields`).

`closures.py` 와 같은 모양이다: 목록을 읽지 못하거나 모양이 틀리면 예외를 그대로 올린다. 빈 목록으로
통과시키면 가드가 조용히 꺼진다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.rules.term_match import Term, TermMatcher, normalize, prepare_fields

GUARD_FILE = "growth_guard.yaml"
GUARD_KINDS = ("diagnosis", "coercion", "commerce")
_TERM_KEYS = frozenset({"label", "aliases", "guards"})


@dataclass(frozen=True)
class GuardVerdict:
    allowed: bool
    # 걸린 종류 키(diagnosis · coercion · commerce). 후보 원문이 아니다
    hits: tuple[str, ...] = ()


@cache
def guard_terms() -> tuple[Term, ...]:
    """`growth_guard.yaml` 을 읽는다. 읽지 못하거나 모양이 틀리면 예외를 그대로 올린다."""
    return parse_guard_terms(load_reference(GUARD_FILE))


@cache
def _matcher() -> TermMatcher:
    """목록을 한 번만 컴파일한다 — 후보마다 · 재호출마다 별칭을 다시 만들지 않는다."""
    return TermMatcher(guard_terms(), cross_spaces=False)


def parse_guard_terms(data: dict[str, Any]) -> tuple[Term, ...]:
    """종류마다 항목을 `Term(key=종류)` 로 옮긴다. 한 글자 별칭은 guard 가 있어야 한다."""
    missing = [kind for kind in GUARD_KINDS if kind not in data]
    if missing:
        raise ValueError(f"{GUARD_FILE} 에 목록이 없다: {missing}")
    seen: set[str] = set()
    terms: list[Term] = []
    for kind in GUARD_KINDS:
        entries = data[kind] or []
        if not entries:
            raise ValueError(f"{GUARD_FILE} {kind} 가 비었다")
        for raw in entries:
            if extra := set(raw) - _TERM_KEYS:
                raise ValueError(f"{GUARD_FILE} {kind} 에 정해지지 않은 칸이 있다: {sorted(extra)}")
            label = raw.get("label")
            aliases = tuple(raw.get("aliases") or ())
            guards = tuple(raw.get("guards") or ())
            if not label or not aliases:
                raise ValueError(f"{GUARD_FILE} {kind} 의 항목에 label 이나 aliases 가 비었다")
            if label in seen:
                raise ValueError(f"{GUARD_FILE} 에 label 이 겹친다: {label!r}")
            seen.add(label)
            for alias in aliases:
                if len(normalize(alias)) < 2 and not guards:
                    raise ValueError(
                        f"{GUARD_FILE} {label!r} 의 한 글자 별칭 {alias!r} 에 guard 가 없다 "
                        "— 과차단"
                    )
            terms.append(Term(key=kind, aliases=aliases, guards=guards))
    return tuple(terms)


def check_growth_text(texts: Sequence[str], *, terms: Sequence[Term] | None = None) -> GuardVerdict:
    """칸마다 따로 대조해 걸린 종류를 돌려준다. `terms` 를 안 주면 실제 목록이다."""
    matcher = _matcher() if terms is None else TermMatcher(terms, cross_spaces=False)
    hits = tuple(dict.fromkeys(matcher.match(prepare_fields(tuple(texts)))))
    return GuardVerdict(allowed=not hits, hits=hits)
