"""보호자가 적은 알레르기 · 질환 이름을 막을 것으로 바꾸는 공통 규칙 (#258).

health_safety.label 은 자유 입력이다. 고르는 목록이 없고, 검사지 사진에서 옮긴 이름(영어 포함)도
그대로 들어온다. "우유 알레르기" · "우유(유제품)" · "우유, 계란" · "달걀흰자" · "Egg white" 가
다 같은 아이 정보라서, 이름 하나가 정확히 별칭과 같을 때만 코드로 읽으면 나머지는 조용히 통과한다.

이 모듈은 이름을 조각으로 나눠 이름 사전에서 찾는다. 정확히 같은 이름이 있으면 그것을 쓰고,
없으면 조각 안에서 사전의 이름을 찾는다(guard 를 지킨다). 사전은 호출하는 Agent 가 만든다 —
공용 이름 사전(allergen_terms.yaml)에 Food 는 자기 음식 어휘를 더하고, Activity · Growth 는
19종 밖 식품 별칭만 더한 `allergy_book()` 을 쓴다.

식품 사전에 없는 알레르기 이름(꽃가루 · 라텍스)에 붙이는 확인 문구도 여기서 고른다
(`non_food_allergies`). 문구 자체는 Agent 마다 자기 `*.readout.yaml` 에 둔다.

Food · Activity · Growth 가 같이 쓴다. food 패키지를 import 하지 않는다 (docs/agents/README.md §6).
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Literal, Protocol

from app.agents.common.reference import allergen_groups, allergen_terms, food_safety_terms
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

# 놀이 · 교육 문장(Activity · Growth)에서 대조하는 한 글자 이름 (#261 · #305). 그 밖의 한 글자는
# 사전 별칭이든 보호자가 적은 이름이든 문장에 대조하지 않는다 — "밀" · "게" · "굴" · "닭" 은
# "공 밀기" · "카드 게임" · "굴리기" · "닭 그림" 이, 강아지 알레르기를 적은 "개" 는 "블록 3개" 가
# 걸린다. 이 글자들은 놀이 문장에서도 거의 그것이다 — "콩주머니" 에는 실제 콩이 들고, 쌀 촉감놀이 ·
# 팥 주머니(오자미)도 영아 놀이로 흔하다. 꽃은 보호자가 그 한 글자로 적었으면 꽃(꽃가루) 알레르기로
# 보고 꽃 활동을 막는다(#341 리뷰 — #264 의 "애매하면 막는다").
#
# 값은 그 글자가 다른 뜻으로 나오는 자리(guard)다. 보호자가 적은 이름을 글자 그대로 대조할 때
# 붙인다 — 사전 이름(잣 · 콩)은 사전의 guard 를 쓴다. 그 글자가 든 같은 것(찹쌀 · 꽃가루)은
# guard 에 넣지 않는다(막는 쪽). 쌀의 좁쌀 · 보리쌀은 Food 사전(food_safety_terms.yaml)의 쌀과 같다.
# 메뉴를 보는 Food 는 자기 목록(`food/tools/safety.SINGLE_CHAR_FOODS`)을 쓰고, 이 글자들도
# 다 막는다(잣 · 콩 · 깨 · 쌀 · 팥은 Food 사전 이름, 쑥 · 꽃은 그 목록).
SINGLE_CHAR_NAMES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "잣": (),
        "콩": (),
        "깨": ("깨끗", "깨우", "깨워", "깨면", "깨진", "깨지", "깨져", "으깨"),
        "쑥": ("쑥쑥",),
        "쌀": ("쌀쌀", "좁쌀", "보리쌀"),
        "팥": (),
        "꽃": ("꽃게", "불꽃", "눈꽃", "꽃샘"),
    }
)


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


@cache
def allergy_book() -> LabelBook:
    """Activity · Growth 가 보호자의 알레르기 이름을 읽는 사전 (#437).

    공용 이름 사전에 **19종 밖 알레르기 식품**(키위 · 귀리 · 녹두 · 버섯 …)의 별칭을 더한다.
    `shared_book()` 만 쓰면 "키위" 로 등록한 아이의 "참다래" 놀이가 막히지 않고, 사전에 없는 식품을
    꽃가루처럼 "식품 아닌 알레르기" 로 봐서 결과마다 확인 문구가 붙는다 (QA 16 — 버섯류).
    별칭 표는 `food_safety_terms.yaml` 의 `extra_allergens` · `group_foods` 로 공용 로더가 읽는다 —
    food 패키지를 import 하지 않는다. Food 의 음식 이름 어휘(빵 · 라면)는 더하지 않는다 — 놀이 문장
    검사까지 넓어진다(allergen_terms.yaml 머리말).
    """
    food = food_safety_terms()
    rules = shared_name_rules(food.group_foods)
    for raw in food.extras:
        term = _with_sentence_guards(raw)
        rules.extend((name, Restriction(terms=(term,)), term.guards) for name in term.aliases)
    return make_book(rules)


def _with_sentence_guards(term: Term) -> Term:
    """한 글자 별칭(참깨의 "깨" · 쌀의 "쌀")에 놀이 · 교육 문장용 guard 를 더한다.

    별칭 표의 guard 는 메뉴 이름용이라 "잠 깨우기" · "쌀쌀한 날씨" 를 덮지 못한다. 놀이 문장에서
    한 글자를 대조할 때 쓰는 guard(`SINGLE_CHAR_NAMES`)를 같이 붙인다 — 더 덮는 쪽이지만 그 자리는
    그 식품이 아닌 자리만 모은 것이다.
    """
    extra = [
        guard
        for alias in term.aliases
        for guard in SINGLE_CHAR_NAMES.get(normalize(alias), ())
        if guard not in term.guards
    ]
    if not extra:
        return term
    return Term(key=term.key, aliases=term.aliases, guards=(*term.guards, *dict.fromkeys(extra)))


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


class SafetyRow(Protocol):
    """`health_safety` 한 행에서 이 모듈이 읽는 칸. `SafetyEntry` 는 Agent 마다 따로 둔다."""

    @property
    def kind(self) -> str: ...

    @property
    def label(self) -> str: ...

    @property
    def status(self) -> str: ...


def non_food_names(label: str, book: LabelBook) -> tuple[str, ...]:
    """이름 하나에서 식품 사전이 아무것도 못 찾은 조각(정규화한 글자). 다 식품이면 빈 튜플.

    조각마다 읽는다 — "땅콩, 꽃가루" 의 꽃가루를 땅콩이 덮지 않게. 조각 안에서 사전 이름을
    찾으면("달걀흰자" 의 달걀) 식품이다. 숫자처럼 이름이 아닌 조각("우유(2)" 의 2)은 세지 않는다.
    """
    names: list[str] = []
    for piece in split_label(label):
        found, rest = read_label(piece, book)
        if rest and not found.codes and not found.terms:
            names.append(piece)
    return tuple(names)


def non_food_allergies(rows: Iterable[SafetyRow], book: LabelBook) -> tuple[str, ...]:
    """확인 문구를 붙일 알레르기 이름 — 보호자가 적은 그대로, 같은 이름은 한 번.

    `allergy` 이고 active인 행 중 이름에 식품 사전이 못 찾은 조각이 있는 것이다. 꽃가루 · 라텍스 ·
    동물털처럼 식품이 아닌 알레르기는 뺄 활동을 코드가 못 정한다 — 이름을 물건 · 장소로 넓히는
    대응표(꽃가루 → 공원)는 판단을 담게 된다. 그래서 적은 이름을 되돌려 보여 주고 확인하게 할
    뿐이다. 19종 밖 식품도 별칭 표(`extra_allergens`)에 있으면 `allergy_book()` 이 식품으로
    읽어 빠지고(키위 · 버섯), 표에 없는 것(쑥)만 들어간다. 식품 알레르기는 이름 대조가 뺀다.

    `environmental`은 고소공포 같은 것이라(data_model.md) 알레르기 문구가 맞지 않아 넣지 않는다.
    `health_safety.category`가 빠진 뒤로는 환경 알레르기만 고를 칸이 없어서 사전으로 가른다.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for row in rows:
        key = normalize(row.label)
        if row.kind != "allergy" or row.status != "active" or key in seen:
            continue
        if non_food_names(row.label, book):
            seen.add(key)
            labels.append(row.label)
    return tuple(labels)


AllergenState = Literal["active", "known"] | None


def allergen_states(
    allergens: Iterable[str], rows: Iterable[tuple[str, str, str]], book: LabelBook
) -> dict[str, AllergenState]:
    """추천에 든 항목마다 그 아이의 health_safety 상태. 05 의 사전검사가 쓴다 (#249).

    `rows` 는 (kind, label, status) — 모든 kind 다. Activity 는 19종 밖 이름(꽃가루)을 등록된
    이름 그대로 싣고, 그 행은 environmental 일 수 있다. 같은 이름 사전(`read_label`)으로 양쪽을
    읽어 코드 · Term 이 겹치거나 사전에 없는 조각이 같으면 같은 항목이다.
    - active 행에 걸리면 "active" — 그 추천은 내보내지 않는다.
    - none · retracted 행에만 걸리면 "known" — 묻지 않는다.
    - 걸리는 행이 없으면 None — 채택 전에 묻는다.
    """
    read = [(status, *read_label(label, book)) for _, label, status in rows]
    states: dict[str, AllergenState] = {}
    for name in allergens:
        wanted, rest = read_label(name, book)
        hits = [
            status
            for status, found, found_rest in read
            if wanted.codes & found.codes
            or set(wanted.terms) & set(found.terms)
            or set(rest) & set(found_rest)
        ]
        states[name] = "active" if "active" in hits else ("known" if hits else None)
    return states


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
