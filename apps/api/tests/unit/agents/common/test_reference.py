"""app/agents/common/reference.py — YAML 참조 상수 로더.

allergen_terms.yaml 은 알레르기 필터의 근거표라 형태가 깨지면 조용히 항목을
놓칠 수 있다. 그래서 코드 집합·label 자가 포함 여부를 직접 검증한다.

chronic_restriction.yaml 은 만성질환·식이제한의 유일한 입력 경로다(task-5-brief.md).
정규화 키·중복·빈 항목이 조용히 깨지면 유당불내증 같은 항목이 통째로 안 걸릴 수 있다.
"""

import pytest

from app.agents.common import reference
from app.agents.common.reference import allergen_terms, chronic_restriction_terms, load_reference
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.term_match import normalize


def test_allergen_terms_key_set_matches_allergen_names() -> None:
    terms = allergen_terms()

    assert {term.key for term in terms} == {str(code) for code in ALLERGEN_NAMES}


def test_allergen_terms_every_label_is_in_its_own_aliases() -> None:
    data = load_reference("allergen_terms.yaml")

    for row in data["terms"]:
        assert row["label"] in row["aliases"], f"code {row['code']} label 이 aliases 에 없다"


def test_allergen_terms_is_cached_same_object() -> None:
    assert allergen_terms() is allergen_terms()


def test_load_reference_missing_fetched_at_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "broken.yaml").write_text(
        "source: 테스트\nversion: '1'\nterms: []\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="fetched_at"):
        load_reference("broken.yaml")


def test_load_reference_missing_all_required_keys_lists_them(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "empty.yaml").write_text("terms: []\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_reference("empty.yaml")

    assert "source" in str(exc_info.value)
    assert "version" in str(exc_info.value)
    assert "fetched_at" in str(exc_info.value)


# ── chronic_restriction_terms ────────────────────────────────────────────


def test_chronic_restriction_terms_keys_are_all_normalized() -> None:
    terms = chronic_restriction_terms()

    for key in terms:
        assert key == normalize(key)


def test_chronic_restriction_terms_wheat_guards_include_milk() -> None:
    terms = chronic_restriction_terms()

    celiac = terms[normalize("셀리악병")]
    assert "밀크" in celiac[0].guards


def test_chronic_restriction_terms_duplicate_normalized_label_raises(tmp_path, monkeypatch) -> None:
    """ "유당불내증"과 "유당 불내증"은 normalize()를 거치면 같은 키다 — 겹치면 ValueError."""
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "chronic_restriction.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-09-28\n"
        "restrictions:\n"
        "  - labels: [유당불내증]\n    foods: [우유]\n    guards: []\n"
        "  - labels: [유당 불내증]\n    foods: [분유]\n    guards: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="겹친다"):
        reference.chronic_restriction_terms.__wrapped__()


def test_chronic_restriction_terms_empty_labels_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "chronic_restriction.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-09-28\n"
        "restrictions:\n"
        "  - labels: []\n    foods: [우유]\n    guards: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="labels"):
        reference.chronic_restriction_terms.__wrapped__()


def test_chronic_restriction_terms_empty_foods_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "chronic_restriction.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-09-28\n"
        "restrictions:\n"
        "  - labels: [유당불내증]\n    foods: []\n    guards: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="foods"):
        reference.chronic_restriction_terms.__wrapped__()
