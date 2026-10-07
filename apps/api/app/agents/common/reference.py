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
        terms.append(Term(key=code, aliases=aliases, guards=tuple(row.get("guards", ()))))

    expected = {str(c) for c in ALLERGEN_NAMES}
    if codes_seen != expected:
        raise ValueError(
            "allergen_terms.yaml 의 코드 집합이 ALLERGEN_NAMES와 다르다: "
            f"빠짐={sorted(expected - codes_seen)} 범위밖={sorted(codes_seen - expected)}"
        )
    return tuple(terms)


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
        # Term.key 에 foods 의 용어를 쓰면 hits 에 식품명이 그대로 담긴다.
        # 그래서 이 질환 항목의 첫 labels 값을 key 로 쓴다.
        terms = (Term(key=labels[0], aliases=foods, guards=tuple(row.get("guards", ()))),)
        # 같은 항목 안의 표기끼리는 정규화하면 겹칠 수 있다("유당불내증"·"유당 불내증").
        # 그건 같은 질환의 중복일 뿐이라 에러가 아니다 — set 으로 먼저 모아 흡수한다.
        # 겹치면 안 되는 건 "다른 항목"이 같은 정규화 라벨을 갖는 경우뿐이다.
        row_keys = {normalize(label) for label in labels}
        for key in row_keys:
            if key in result:
                raise ValueError(f"chronic_restriction.yaml 에 정규화 라벨이 겹친다: {key!r}")
            result[key] = terms
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
