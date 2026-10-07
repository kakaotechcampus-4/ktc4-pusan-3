"""`apps/api/reference/*.yaml` 참조 상수 로더.

YAML 파싱은 표준 라이브러리가 아니라서 agents 레이어에 둔다. 매칭 자체는
`app/rules/term_match.py`의 순수 함수가 하고, 이 모듈은 YAML → `Term` 변환만 맡는다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.rules.allergen import ALLERGEN_NAMES
from app.rules.term_match import Term, normalize

REFERENCE_DIR = Path(__file__).resolve().parents[3] / "reference"  # apps/api/reference

_REQUIRED_KEYS = ("source", "version", "fetched_at")


def load_reference(name: str) -> dict[str, Any]:
    """`apps/api/reference/{name}` 을 읽는다.

    `source`·`version`·`fetched_at`이 없으면 출처를 알 수 없는 상수표라 `ValueError`.
    안전 판정에 쓰는 표라 어디서 온 값인지 항상 추적 가능해야 한다.
    """
    path = REFERENCE_DIR / name
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise ValueError(f"{name} 에 필수 키가 없다: {missing}")
    return data


@cache
def allergen_terms() -> tuple[Term, ...]:
    """`allergen_terms.yaml` 을 `Term` 튜플로. `key` 는 알레르기 코드 문자열("1".."19")."""
    data = load_reference("allergen_terms.yaml")
    terms: list[Term] = []
    codes_seen: set[str] = set()
    for row in data.get("terms", []):
        code = str(row["code"])
        codes_seen.add(code)
        aliases = tuple(row.get("aliases", ()))
        label = row.get("label", "")
        if label and label not in aliases:
            raise ValueError(f"allergen_terms.yaml 코드 {code} 의 aliases 에 label 자신이 없다")
        guards = tuple(row.get("guards", ()))
        _check_guards(f"allergen_terms.yaml 코드 {code}", aliases, guards)
        terms.append(Term(key=code, aliases=aliases, guards=guards))

    expected = {str(c) for c in ALLERGEN_NAMES}
    if codes_seen != expected:
        raise ValueError(
            "allergen_terms.yaml 의 코드 집합이 ALLERGEN_NAMES와 다르다: "
            f"빠짐={sorted(expected - codes_seen)} 범위밖={sorted(codes_seen - expected)}"
        )
    return tuple(terms)


def _check_guards(where: str, aliases: tuple[str, ...], guards: tuple[str, ...]) -> None:
    """guard 는 자기 별칭 하나를 덮어야 하고, 별칭과 같으면 안 된다.

    아무것도 안 덮으면 오타이거나 다른 항목에 붙은 것이다. 별칭과 같으면 그 별칭이 나온 자리를
    전부 덮어 별칭이 통째로 꺼진다 — "꿀" guard 면 꿀 규칙이 사라진다.
    """
    names = [name for name in map(normalize, aliases) if name]
    for guard in guards:
        if normalize(guard) in names:
            raise ValueError(f"{where} 의 guard {guard!r} 가 별칭과 같다 — 그 별칭이 꺼진다")
        if not any(name in normalize(guard) for name in names):
            raise ValueError(f"{where} 의 guard {guard!r} 가 자기 별칭을 덮지 않는다")


def _check_keys(where: str, raw: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if extra := set(raw) - allowed:
        raise ValueError(f"{where} 에 정해지지 않은 칸이 있다: {sorted(extra)}")


def _check_codes(where: str, codes: Any) -> frozenset[int]:
    """19종 코드 목록. 정수가 아니거나 범위 밖이면 `ValueError`."""
    values = list(codes or ())
    if outside := [c for c in values if isinstance(c, bool) or c not in ALLERGEN_NAMES]:
        raise ValueError(f"{where} 에 19종 밖 코드가 있다: {outside}")
    return frozenset(values)


# allergen_terms.yaml groups — 묶음 이름
_GROUP_KEYS = frozenset({"label", "aliases", "codes", "includes", "guards"})


@dataclass(frozen=True)
class AllergenGroup:
    """묶음 이름 하나("갑각류"). 보호자가 등록한 이름을 읽을 때만 쓴다.

    `codes` · `members` 는 includes 를 다 펼친 값이다. `members` 는 자기 자신과 포함한 묶음
    label 이고, Food 가 묶음의 19종 밖 식품(group_foods)을 붙일 때 쓴다.
    """

    label: str
    aliases: tuple[str, ...]
    codes: frozenset[int]
    members: tuple[str, ...]
    guards: tuple[str, ...]


@cache
def allergen_groups() -> tuple[AllergenGroup, ...]:
    """`allergen_terms.yaml` 의 `groups`. 읽지 못하거나 모양이 틀리면 예외를 그대로 올린다."""
    return parse_allergen_groups(load_reference("allergen_terms.yaml"), allergen_terms())


def parse_allergen_groups(
    data: Mapping[str, Any], terms: tuple[Term, ...]
) -> tuple[AllergenGroup, ...]:
    """`groups` 를 검사해 펼친다. 틀린 곳이 있으면 `ValueError`.

    묶음 이름이 19종 별칭과 같으면(콩) 그 별칭의 코드를 다 포함해야 한다 — 묶음이 더 좁으면
    같은 이름이 등록 방법에 따라 덜 막힌다.
    """
    alias_codes: dict[str, set[int]] = {}
    for term in terms:
        for alias in term.aliases:
            alias_codes.setdefault(normalize(alias), set()).add(int(term.key))

    by_label: dict[str, Mapping[str, Any]] = {}
    for raw in data.get("groups") or []:
        label = raw.get("label")
        _check_keys(f"allergen_terms.yaml 묶음 {label!r}", raw, _GROUP_KEYS)
        if not label:
            raise ValueError("allergen_terms.yaml 묶음에 label 이 없다")
        if label in by_label:
            raise ValueError(f"allergen_terms.yaml 에 묶음 label 이 겹친다: {label!r}")
        by_label[label] = raw

    def unfold(label: str, path: tuple[str, ...]) -> tuple[frozenset[int], tuple[str, ...]]:
        if label in path:
            raise ValueError(f"allergen_terms.yaml 묶음 includes 가 돌아온다: {[*path, label]}")
        raw = by_label[label]
        codes = set(_check_codes(f"allergen_terms.yaml 묶음 {label!r}", raw.get("codes")))
        members = [label]
        for inner in raw.get("includes") or ():
            if inner not in by_label:
                raise ValueError(
                    f"allergen_terms.yaml 묶음 {label!r} 이 없는 묶음 {inner!r} 을 포함한다"
                )
            inner_codes, inner_members = unfold(inner, (*path, label))
            codes |= inner_codes
            members += [m for m in inner_members if m not in members]
        return frozenset(codes), tuple(members)

    groups: list[AllergenGroup] = []
    owner: dict[str, str] = {}
    for label, raw in by_label.items():
        where = f"allergen_terms.yaml 묶음 {label!r}"
        aliases = tuple(raw.get("aliases") or ())
        if label not in aliases:
            raise ValueError(f"{where} 의 aliases 에 label 자신이 없다")
        codes, members = unfold(label, ())
        if not codes:
            raise ValueError(f"{where} 에 codes 가 없다(includes 를 펼쳐도)")
        for alias in aliases:
            key = normalize(alias)
            if key in owner:
                raise ValueError(
                    f"allergen_terms.yaml 묶음 이름이 겹친다: {alias!r} ({owner[key]} · {label})"
                )
            owner[key] = label
            if not alias_codes.get(key, set()) <= codes:
                raise ValueError(
                    f"{where} 의 이름 {alias!r} 은 19종 별칭이기도 한데 그 코드를 포함하지 않는다"
                )
        guards = tuple(raw.get("guards") or ())
        _check_guards(where, aliases, guards)
        groups.append(AllergenGroup(label, aliases, codes, members, guards))
    return tuple(groups)


# food_safety_terms.yaml — Food 전용 음식 어휘

AgeAction = Literal["block", "caution"]
AgeScope = Literal["all", "name"]  # all — 메뉴명 · 재료 전부, name — 메뉴명만

_FOOD_TOP_KEYS = frozenset(
    {"source", "version", "fetched_at", "code_foods", "extra_allergens", "group_foods", "age_rules"}
)
_CODE_FOOD_KEYS = frozenset({"code", "foods", "guards"})
_EXTRA_KEYS = frozenset({"label", "aliases", "guards"})
_GROUP_FOOD_KEYS = frozenset({"group", "foods", "guards"})
_AGE_RULE_KEYS = frozenset(
    {"label", "below_month", "action", "scope", "aliases", "guards", "exact", "ends", "source"}
)


@dataclass(frozen=True)
class AgeRule:
    """연령 · 아이 금지 식품 규칙 하나. `term.key` 는 규칙 label 이다."""

    term: Term
    below_month: int | None  # None 이면 모든 아이
    action: AgeAction
    exact: frozenset[str]  # 정규화한 메뉴명. 메뉴명이 이 글자 그대로일 때만 건다
    source: str
    # name 이면 메뉴명만 본다 — 마시는 것을 막는 규칙(술 · 카페인)이 재료 속 양념에 걸리지 않게
    scope: AgeScope = "all"
    # 정규화한 메뉴명이 이 글자로 끝나면 건다 — "검은콩 우유" 는 마시는 우유, "우유푸딩" 은 아니다
    ends: frozenset[str] = frozenset()

    def applies_at(self, months: int) -> bool:
        return self.below_month is None or months < self.below_month


@dataclass(frozen=True)
class FoodSafetyTerms:
    """`food_safety_terms.yaml` 을 읽은 결과."""

    code_terms: tuple[Term, ...]  # key 는 19종 코드 문자열 — 메뉴를 훑어 코드를 붙인다
    extras: tuple[Term, ...]  # key 는 19종 밖 알레르기 label
    group_foods: Mapping[str, Term]  # 묶음 label → 그 묶음의 19종 밖 식품(key 는 묶음 label)
    age_rules: tuple[AgeRule, ...]


@cache
def food_safety_terms() -> FoodSafetyTerms:
    """Food 전용 음식 어휘. 읽지 못하거나 모양이 틀리면 예외를 그대로 올린다.
    빈 어휘로 넘기면 메뉴가 조용히 통과한다."""
    return parse_food_safety_terms(
        load_reference("food_safety_terms.yaml"), allergen_terms(), allergen_groups()
    )


def parse_food_safety_terms(
    data: Mapping[str, Any], terms: tuple[Term, ...], groups: tuple[AllergenGroup, ...]
) -> FoodSafetyTerms:
    """YAML 을 읽은 dict 를 검사해 `FoodSafetyTerms` 로. 틀린 곳이 있으면 `ValueError`."""
    _check_keys("food_safety_terms.yaml", data, _FOOD_TOP_KEYS)
    shared = {normalize(a) for term in terms for a in term.aliases}
    shared |= {normalize(a) for group in groups for a in group.aliases}

    code_terms: list[Term] = []
    for raw in data.get("code_foods") or []:
        where = f"food_safety_terms.yaml code_foods {raw.get('code')!r}"
        _check_keys(where, raw, _CODE_FOOD_KEYS)
        code = _check_codes(where, [raw.get("code")])
        if str(raw["code"]) in {t.key for t in code_terms}:
            raise ValueError(f"{where} 가 두 번 있다")
        foods = tuple(raw.get("foods") or ())
        if not foods:
            raise ValueError(f"{where} 의 foods 가 비어 있다")
        guards = tuple(raw.get("guards") or ())
        _check_guards(where, foods, guards)
        code_terms.append(Term(key=str(next(iter(code))), aliases=foods, guards=guards))

    extras: list[Term] = []
    owner: dict[str, str] = {}
    for raw in data.get("extra_allergens") or []:
        label = raw.get("label")
        where = f"food_safety_terms.yaml extra_allergens {label!r}"
        _check_keys(where, raw, _EXTRA_KEYS)
        aliases = tuple(raw.get("aliases") or ())
        if not label or label not in aliases:
            raise ValueError(f"{where} 의 aliases 에 label 자신이 없다")
        for alias in aliases:
            key = normalize(alias)
            if key in shared:
                raise ValueError(f"{where} 의 {alias!r} 는 공용 이름 사전(19종 · 묶음)에 있다")
            if key in owner:
                raise ValueError(f"food_safety_terms.yaml 19종 밖 이름이 겹친다: {alias!r}")
            owner[key] = label
        guards = tuple(raw.get("guards") or ())
        _check_guards(where, aliases, guards)
        extras.append(Term(key=label, aliases=aliases, guards=guards))

    group_labels = {group.label for group in groups}
    group_foods: dict[str, Term] = {}
    for raw in data.get("group_foods") or []:
        label = raw.get("group")
        where = f"food_safety_terms.yaml group_foods {label!r}"
        _check_keys(where, raw, _GROUP_FOOD_KEYS)
        if label not in group_labels:
            raise ValueError(f"{where} 는 allergen_terms.yaml 에 없는 묶음이다")
        if label in group_foods:
            raise ValueError(f"{where} 가 두 번 있다")
        foods = tuple(raw.get("foods") or ())
        if not foods:
            raise ValueError(f"{where} 의 foods 가 비어 있다")
        guards = tuple(raw.get("guards") or ())
        _check_guards(where, foods, guards)
        group_foods[label] = Term(key=label, aliases=foods, guards=guards)

    age_rules: list[AgeRule] = []
    for raw in data.get("age_rules") or []:
        label = raw.get("label")
        where = f"food_safety_terms.yaml age_rules {label!r}"
        _check_keys(where, raw, _AGE_RULE_KEYS)
        below = raw.get("below_month")
        if below is not None and (
            isinstance(below, bool) or not isinstance(below, int) or below < 1
        ):
            raise ValueError(
                f"{where} 의 below_month 는 1 이상 정수거나 null 이어야 한다: {below!r}"
            )
        action = raw.get("action")
        if action not in ("block", "caution"):
            raise ValueError(f"{where} 의 action 은 block · caution 중 하나다: {action!r}")
        scope = raw.get("scope", "all")
        if scope not in ("all", "name"):
            raise ValueError(f"{where} 의 scope 는 all · name 중 하나다: {scope!r}")
        aliases = tuple(raw.get("aliases") or ())
        if not label or not aliases:
            raise ValueError(f"{where} 에 label 이나 aliases 가 없다")
        source = str(raw.get("source") or "").strip()
        if not source:
            raise ValueError(f"{where} 에 source 가 없다")
        guards = tuple(raw.get("guards") or ())
        _check_guards(where, aliases, guards)
        exact = frozenset(normalize(name) for name in raw.get("exact") or () if normalize(name))
        ends = frozenset(normalize(name) for name in raw.get("ends") or () if normalize(name))
        if label in {rule.term.key for rule in age_rules}:
            raise ValueError(f"{where} 가 두 번 있다")
        term = Term(key=label, aliases=aliases, guards=guards)
        age_rules.append(AgeRule(term, below, action, exact, source, scope, ends))

    return FoodSafetyTerms(tuple(code_terms), tuple(extras), group_foods, tuple(age_rules))


@cache
def chronic_restriction_terms() -> dict[str, tuple[Term, ...]]:
    """`chronic_restriction.yaml` 을 정규화한 질환 라벨 → 제한 식품 `Term` 으로.

    키는 `normalize(label)` 이다 — 보호자가 "유당 불내증" 처럼 공백을 넣어 적어도 맞는다.
    한 질환의 여러 표기가 같은 `Term` 튜플을 가리킨다.
    """
    data = load_reference("chronic_restriction.yaml")
    result: dict[str, tuple[Term, ...]] = {}
    for row in data.get("restrictions", []):
        labels = tuple(row.get("labels", ()))
        foods = tuple(row.get("foods", ()))
        if not labels or not foods:
            raise ValueError(
                f"chronic_restriction.yaml 의 항목에 labels 나 foods 가 비어 있다: {row!r}"
            )
        guards = tuple(row.get("guards", ()))
        _check_guards(f"chronic_restriction.yaml {labels[0]!r}", foods, guards)
        # Term.key 에 foods 의 용어를 쓰면 hits 에 식품명이 그대로 담긴다.
        # 그래서 이 질환 항목의 첫 labels 값을 key 로 쓴다.
        terms = (Term(key=labels[0], aliases=foods, guards=guards),)
        # 같은 항목 안의 표기끼리는 정규화하면 겹칠 수 있다("유당불내증"·"유당 불내증").
        # 그건 같은 질환의 중복일 뿐이라 에러가 아니다 — set 으로 먼저 모아 흡수한다.
        # 겹치면 안 되는 건 "다른 항목"이 같은 정규화 라벨을 갖는 경우뿐이다.
        row_keys = {normalize(label) for label in labels}
        for key in row_keys:
            if key in result:
                raise ValueError(f"chronic_restriction.yaml 에 정규화 라벨이 겹친다: {key!r}")
            result[key] = terms
    return result


@cache
def chronic_restriction_codes() -> dict[str, frozenset[int]]:
    """`chronic_restriction.yaml`의 `codes`: 정규화 질환 라벨 → 막을 19종 코드로 변환하는 함수.

    질환 정의상 그 성분을 통째로 빼야 하는 것만 적는다(유당불내증 → 우유, 셀리악병 → 밀).
    foods 만 쓰면 그 목록에 없는 같은 성분 음식(버터 · 소면)이 빠진다.
    """
    data = load_reference("chronic_restriction.yaml")
    result: dict[str, frozenset[int]] = {}
    for row in data.get("restrictions", []):
        codes = _check_codes(f"chronic_restriction.yaml {row.get('labels')!r}", row.get("codes"))
        for label in row.get("labels", ()):
            result[normalize(label)] = codes
    return result


# ── hazard_terms.yaml ──────────────────────────────────────────────────────

HazardLevel = Literal["block", "warn"]

_AXIS_KEYS = frozenset({"block_below_month", "warn_below_month", "source", "warning_text"})
_TERM_KEYS = frozenset({"axis", "label", "aliases", "guards"})


@dataclass(frozen=True)
class HazardAxis:
    """위험 축 하나. 월령 · 출처 · 경고 문구는 축에만 있다 — 용어는 축을 가리키기만 한다 (3-5)."""

    name: str
    block_below_month: int | None
    warn_below_month: int | None
    source: str
    warning_text: str | None  # 화면 경고 문구. LLM 이 쓰지 않는다

    def level_at(self, months: int) -> HazardLevel | None:
        """이 월령에서 차단인가 경고인가. 0–17개월 경고 → 차단 승격은 Activity 코드가 한다."""
        if self.block_below_month is not None and months < self.block_below_month:
            return "block"
        if self.warn_below_month is not None and months < self.warn_below_month:
            return "warn"
        return None


@dataclass(frozen=True)
class HazardTerms:
    """`hazard_terms.yaml` 을 읽은 결과. `terms` 의 `Term.key` 는 용어 label 이다."""

    axes: Mapping[str, HazardAxis]
    terms: tuple[Term, ...]
    term_axis: Mapping[str, str]  # label → 축 이름

    def axis_of(self, label: str) -> HazardAxis:
        return self.axes[self.term_axis[label]]


@cache
def hazard_terms() -> HazardTerms:
    """`hazard_terms.yaml` — Activity 안전 필터와 Growth 적재 lint 가 같이 읽는다.

    읽지 못하거나 모양이 틀리면 예외를 그대로 올린다. 빈 사전으로 통과시키면 전 후보가
    조용히 나간다.
    """
    return parse_hazard_terms(load_reference("hazard_terms.yaml"))


def parse_hazard_terms(data: Mapping[str, Any]) -> HazardTerms:
    """YAML 을 읽은 dict 를 검사해 `HazardTerms` 로. 틀린 곳이 있으면 `ValueError`."""
    axes = {name: _parse_axis(name, raw) for name, raw in (data.get("axes") or {}).items()}
    pending = set(data.get("pending_axes") or {})
    if both := pending & set(axes):
        raise ValueError(
            f"hazard_terms.yaml 의 pending_axes 와 axes 에 같은 축이 있다: {sorted(both)}"
        )

    terms: list[Term] = []
    term_axis: dict[str, str] = {}
    alias_owner: dict[str, str] = {}
    for raw in data.get("terms") or []:
        label = raw.get("label")
        if extra := set(raw) - _TERM_KEYS:
            raise ValueError(
                f"hazard_terms.yaml 용어 {label!r} 에 정해지지 않은 칸이 있다: {sorted(extra)}"
            )
        axis = raw.get("axis")
        if axis not in axes:
            raise ValueError(f"hazard_terms.yaml 용어 {label!r} 가 없는 축 {axis!r} 을 가리킨다")
        aliases = tuple(raw.get("aliases") or ())
        if label not in aliases:
            raise ValueError(f"hazard_terms.yaml 용어 {label!r} 의 aliases 에 label 자신이 없다")
        if label in term_axis:
            raise ValueError(f"hazard_terms.yaml 에 label 이 겹친다: {label!r}")
        for alias in aliases:
            key = normalize(alias)
            if len(key) < 2:
                raise ValueError(
                    f"hazard_terms.yaml 용어 {label!r} 에 한 글자 별칭 {alias!r} — 과차단"
                )
            if key in alias_owner:
                raise ValueError(
                    f"hazard_terms.yaml 에 별칭이 겹친다: {alias!r} ({alias_owner[key]} · {label})"
                )
            alias_owner[key] = label
        terms.append(Term(key=label, aliases=aliases, guards=tuple(raw.get("guards") or ())))
        term_axis[label] = axis

    if empty := set(axes) - set(term_axis.values()):
        raise ValueError(f"hazard_terms.yaml 에 용어가 없는 축이 있다: {sorted(empty)}")
    return HazardTerms(axes=axes, terms=tuple(terms), term_axis=term_axis)


def _parse_axis(name: str, raw: Mapping[str, Any]) -> HazardAxis:
    if extra := set(raw) - _AXIS_KEYS:
        raise ValueError(f"hazard_terms.yaml 축 {name} 에 정해지지 않은 칸이 있다: {sorted(extra)}")
    block = raw.get("block_below_month")
    warn = raw.get("warn_below_month")
    if block is None and warn is None:
        raise ValueError(f"hazard_terms.yaml 축 {name} 에 월령(block · warn)이 둘 다 없다")
    for value in (block, warn):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"hazard_terms.yaml 축 {name} 의 월령은 정수여야 한다: {value!r}")
    if block is not None and warn is not None and warn < block:
        raise ValueError(f"hazard_terms.yaml 축 {name} 의 warn({warn}) 이 block({block}) 보다 작다")
    source = str(raw.get("source") or "").strip()
    if not source:
        raise ValueError(f"hazard_terms.yaml 축 {name} 에 source 가 없다")
    warning_text = raw.get("warning_text")
    if warn is not None and not (warning_text and str(warning_text).strip()):
        raise ValueError(f"hazard_terms.yaml 축 {name} 은 경고가 있는데 warning_text 가 없다")
    return HazardAxis(
        name=name,
        block_below_month=block,
        warn_below_month=warn,
        source=source,
        warning_text=warning_text,
    )
