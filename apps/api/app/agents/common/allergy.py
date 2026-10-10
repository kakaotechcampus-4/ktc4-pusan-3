"""보호자가 적은 알레르기 · 질환 이름을 막을 것으로 바꾸는 공통 규칙 (#258).

health_safety.label 은 자유 입력이다. 고르는 목록이 없고, 검사지 사진에서 옮긴 이름(영어 포함)도
그대로 들어온다. "우유 알레르기" · "우유(유제품)" · "우유, 계란" · "달걀흰자" · "Egg white" 가
다 같은 아이 정보라서, 이름 하나가 정확히 별칭과 같을 때만 코드로 읽으면 나머지는 조용히 통과한다.

이 모듈은 이름을 조각으로 나눠 이름 사전에서 찾는다. 정확히 같은 이름이 있으면 그것을 쓰고,
없으면 조각 안에서 사전의 이름을 찾는다(guard 를 지킨다). 사전은 호출하는 Agent 가 만든다 —
공용 이름 사전(allergen_terms.yaml)에 Food 는 자기 음식 어휘를 더하고, 더할 것이 없으면
`shared_book()` 을 쓴다.

Food · Activity 가 같이 쓴다. food 패키지를 import 하지 않는다 (docs/agents/README.md §6).
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache

from app.agents.common.reference import allergen_groups, allergen_terms
from app.rules.term_match import Term, TermMatcher, normalize, prepare_fields, split_words


@dataclass(frozen=True)
class Restriction:
    """이름 하나가 막는 것 — 19종 코드와 글자로 찾을 Term."""

    codes: frozenset[int] = frozenset()
    terms: tuple[Term, ...] = ()

    def __or__(self, other: "Restriction") -> "Restriction":
        extra = tuple(term for term in other.terms if term not in self.terms)
        return Restriction(self.codes | other.codes, self.terms + extra)


# (이름, 그 이름이 막는 것, 다른 이름 안에서 이 이름을 찾을 때 덮을 guard)
NameRule = tuple[str, Restriction, tuple[str, ...]]


@dataclass(frozen=True)
class LabelBook:
    """이름 사전. `names` 의 키는 정규화한 이름이다."""

    names: Mapping[str, Restriction]
    # 정확히 같은 이름이 없을 때 조각 안에서 찾는 Term. key 는 names 의 키, aliases 는 사전에
    # 적힌 그대로의 철자다 — "pine nut" 의 띄어쓰기가 남아야 "pine-nut 쿠키" 에서도 찾는다
    scan_terms: tuple[Term, ...]
    matcher: TermMatcher  # scan_terms 의 색인 — guard 가 띄어쓰기를 넘지 않는다


def make_book(rules: Iterable[NameRule]) -> LabelBook:
    """같은 이름이 여러 번 오면 막을 것을 합친다 — 더 막는 쪽이다."""
    names: dict[str, Restriction] = {}
    spellings: dict[str, set[str]] = {}
    guards: dict[str, set[str]] = {}
    for name, restriction, name_guards in rules:
        key = normalize(name)
        if not key:
            continue
        names[key] = names.get(key, Restriction()) | restriction
        spellings.setdefault(key, set()).add(name)
        guards.setdefault(key, set()).update(name_guards)
    scan_terms = tuple(
        Term(key=key, aliases=tuple(sorted(spellings[key])), guards=tuple(sorted(guards[key])))
        for key in names
    )
    return LabelBook(names=names, scan_terms=scan_terms, matcher=TermMatcher(scan_terms))


def shared_name_rules(group_foods: Mapping[str, Term] | None = None) -> list[NameRule]:
    """공용 이름 사전의 이름들. 19종 별칭은 그 코드로, 묶음 이름은 펼친 코드로 읽는다.

    묶음 이름은 그 글자 그대로도 막는다 — 레시피 재료에 "다진 견과류" · "해물믹스" 처럼 묶음 이름이
    그대로 나온다. 포함한 묶음(해산물 ⊃ 생선)의 이름도 같이 막는다.
    group_foods(묶음 label → 그 묶음의 19종 밖 식품)를 주면 묶음 이름에 그 식품도 붙인다.
    """
    rules: list[NameRule] = []
    for term in allergen_terms():
        coded = Restriction(codes=frozenset({int(term.key)}))
        rules.extend((alias, coded, term.guards) for alias in term.aliases)
    foods = group_foods or {}
    groups = allergen_groups()
    own = {group.label: Term(group.label, group.aliases, group.guards) for group in groups}
    for group in groups:
        literal = [own[label] for label in group.members]
        extra = [foods[label] for label in group.members if label in foods]
        grouped = Restriction(codes=group.codes, terms=tuple(dict.fromkeys(literal + extra)))
        rules.extend((alias, grouped, group.guards) for alias in group.aliases)
    return rules


@cache
def shared_book() -> LabelBook:
    """공용 이름 사전만으로 만든 사전. 음식 어휘를 따로 더하지 않는 쪽이 쓴다."""
    return make_book(shared_name_rules())


# 한 칸에 여러 이름을 적은 경우("우유, 땅콩" · "우유(유제품)")를 나눈다.
# 띄어쓰기로는 나누지 않는다 — "달 걀" 처럼 한 이름 안에 띄어쓰기가 들어간다.
# 가운뎃점은 NFKC 를 거친 글자로 적는다 — 천지인 자판의 가운뎃점(U+318D)은 U+119E 가 되고,
# 반각 가운뎃점(U+FF65)은 U+30FB 가 된다. 글머리 기호(•)도 나눈다
_SEPARATORS = re.compile(r"[,/·、;:|+&()\[\]{}<>ᆞ・•‧∙]|\s(?:및|또는)\s")
# 이름 뒤에 붙여 쓰는 말. 긴 것부터 뗀다
_SUFFIXES = (
    "알레르기성",
    "알러지성",
    "알레르기",
    "알러지",
    "앨러지",
    "과민반응",
    "과민증",
    "allergies",
    "allergy",
    "allergic",
    "있습니다",
    "있어요",
    "반응",
    "증상",
    "주의",
    "의심",
    "있음",
)


# 낱말을 하나씩 읽을 때 건너뛰는 말 — 이름이 아니라 정도 · 연결 · 병명을 적은 것이다
_FILLERS = frozenset(
    {
        "등",
        "및",
        "또는",
        "그리고",
        "혹은",
        "외",
        "전체",
        "모두",
        "전부",
        "약간",
        "조금",
        "심함",
        "심해요",
        "가벼움",
        "있음",
        "있어요",
        "있습니다",
        "없음",
        "주의",
        "의심",
        "반응",
        "증상",
        "단계",
        "class",
        "클래스",
        "type",
        "타입",
        "알레르기",
        "알러지",
        "앨러지",
        "allergy",
        "allergies",
        "allergic",
        "과민반응",
        "과민증",
        "질환",
        "질병",
        "disease",
        "불내증",
        "intolerance",
        # 검사지 성분명 앞에 붙는 말("알파-락트알부민") · 영어 연결어
        "알파",
        "베타",
        "감마",
        "오메가",
        "alpha",
        "beta",
        "gamma",
        "omega",
        "and",
        "or",
        "with",
        "the",
        "of",
        "food",
        "foods",
    }
)

# 이름 뒤에 붙는 조사와 앞 글자 받침 조건 — "C" 받침 있음 · "V" 받침 없음 · "VL" 받침 없음이나 ㄹ ·
# None 상관없음. 받침이 맞지 않으면 조사가 아니다("다과" 의 과). 맞는 것을 다 뗀다
# ("고양이랑" → 고양이 · 고양) — 더 막는 쪽이다
_PARTICLES: tuple[tuple[str, str | None], ...] = (
    ("이랑", "C"),
    ("하고", None),
    ("으로", "C"),
    ("에서", None),
    ("에는", None),
    ("에도", None),
    ("과", "C"),
    ("와", "V"),
    ("랑", "V"),
    ("이", "C"),
    ("가", "V"),
    ("은", "C"),
    ("는", "V"),
    ("을", "C"),
    ("를", "V"),
    ("로", "VL"),
    ("에", None),
)
_RIEUL = 8  # 종성 번호 — ㄹ


def split_label(label: str) -> tuple[str, ...]:
    """이름을 조각으로 나눠 정규화하고 뒤에 붙인 말("알레르기")을 뗀다. 빈 조각은 버린다.

    구분 기호를 찾기 전에 NFKC 로 맞춘다 — 전각 괄호("（")도 괄호로 나눈다.
    """
    return tuple(dict.fromkeys(piece for _, piece in _pieces(label)))


def _pieces(label: str) -> list[tuple[str, str]]:
    """(적힌 그대로의 조각, 정규화하고 꼬리말을 뗀 이름). 빈 조각은 버린다."""
    found = []
    for part in _SEPARATORS.split(unicodedata.normalize("NFKC", label)):
        piece = _strip_suffixes(normalize(part))
        if piece:
            found.append((part, piece))
    return found


def _strip_suffixes(piece: str) -> str:
    while True:
        for suffix in _SUFFIXES:
            if piece.endswith(suffix) and len(piece) > len(suffix):
                piece = piece[: -len(suffix)]
                break
        else:
            return piece


def _noise(word: str) -> bool:
    """이름이 아닌 낱말 — 숫자 · 정도/연결 말 · 한글이 아닌 한 글자("Ara h 2" 의 h · "Cow's" 의 s).

    글자 그대로 막으면 그 글자가 든 메뉴가 다 막힌다.
    """
    return (
        not word
        or word.isdigit()
        or word in _FILLERS
        or (len(word) == 1 and _final_consonant(word) is None)
    )


def read_label(label: str, book: LabelBook) -> tuple[Restriction, tuple[str, ...]]:
    """이름 하나를 읽는다 → (막을 것, 사전에서 정확히 같은 이름을 못 찾은 조각 · 낱말).

    이름은 문장부호 · 기호를 빼고 견준다("'우유'" 는 우유). 정확히 같은 이름이 없는 조각도
    그 안에서 사전의 이름을 찾는다("달걀흰자" 의 "달걀"). 이때 guard 는 띄어쓰기를 넘지 않는다 —
    "밀 감자" 의 "밀" 을 guard "밀감" 이 덮지 않는다. 낱말마다 다시 읽고("우유 바나나" 의
    "바나나" · "아보카도-리치" 의 "리치"), 조사를 뗀 이름도 읽는다("아보카도랑" 의 "아보카도").
    문장부호로 이은 조각은 사전 이름과 같아도 낱말로 한 번 더 읽는다 — "땅콩-버터" 가 한 이름인지
    두 이름인지 모른다. 못 찾은 조각 · 낱말은 호출부가 그 글자 그대로도 막는다 — 사전에 없는
    알레르기("키위주스")가 있다. 숫자 · 기호 · 이름이 아닌 말뿐인 조각("우유(2)" 의 2)은 버린다.
    """
    found = Restriction()
    rest: list[str] = []

    def keep(word: str) -> None:
        if word not in rest:
            rest.append(word)

    for part, piece in _pieces(label):
        words = [_strip_suffixes(word) for word in split_words(part)]
        if all(_noise(word) for word in words):
            continue
        exact = book.names.get(piece)
        if exact is not None:
            found |= exact
            if len(words) == len(part.split()):
                continue
        for key in book.matcher.match(prepare_fields((part,))):
            found |= book.names[key]
        if exact is None:
            keep(piece)
        for token in words:
            if _noise(token):
                continue
            named = book.names.get(token)
            if named is not None:
                found |= named
                continue
            stems = _stems(token)
            if any(stem in _FILLERS for stem in stems):
                continue  # "알레르기가" — 조사를 떼면 이름이 아닌 말이다
            named_stems = [book.names[stem] for stem in stems if stem in book.names]
            for restriction in named_stems:
                found |= restriction
            if named_stems:
                continue
            if token != piece:
                keep(token)
            for stem in stems:
                # 받침 없는 한 글자는 남기지 않는다 — "누가" 의 누 · "사랑" 의 사
                if len(stem) > 1 or (_final_consonant(stem) or 0) > 0:
                    keep(stem)
    return found, tuple(rest)


def _stems(word: str) -> list[str]:
    """낱말 끝의 조사를 뗀 이름들. 받침이 맞는 조사마다 하나씩, 꼬리말("알레르기")도 뗀다."""
    stems: list[str] = []
    for particle, need in _PARTICLES:
        if not word.endswith(particle) or len(word) <= len(particle):
            continue
        stem = word[: -len(particle)]
        final = _final_consonant(stem[-1])
        fits = (
            need is None
            or final is None
            or (need == "C" and final != 0)
            or (need == "V" and final == 0)
            or (need == "VL" and final in (0, _RIEUL))
        )
        stem = _strip_suffixes(stem)
        if fits and stem and stem not in stems:
            stems.append(stem)
    return stems


def _final_consonant(ch: str) -> int | None:
    """한글 음절의 종성 번호(0 이면 받침 없음). 한글 음절이 아니면 None — 받침을 모른다."""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28
    return None
