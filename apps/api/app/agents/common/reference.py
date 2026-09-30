"""`apps/api/reference/*.yaml` 참조 상수 로더.

YAML 파싱은 표준 라이브러리가 아니라서 agents 레이어에 둔다. 매칭 자체는
`app/rules/term_match.py`의 순수 함수가 하고, 이 모듈은 YAML → `Term` 변환만 맡는다.
"""

from functools import cache
from pathlib import Path
from typing import Any

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
