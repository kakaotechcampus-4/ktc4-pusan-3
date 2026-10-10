"""app/agents/common/reference.py — YAML 참조 상수 로더.

allergen_terms.yaml 은 알레르기 필터의 근거표라 형태가 깨지면 조용히 항목을
놓칠 수 있다. 그래서 코드 집합·label 자가 포함 여부를 직접 검증한다.

chronic_restriction.yaml 은 만성질환·식이제한의 유일한 입력 경로다(task-5-brief.md).
정규화 키·중복·빈 항목이 조용히 깨지면 유당불내증 같은 항목이 통째로 안 걸릴 수 있다.
"""

import copy

import pytest

from app.agents.common import reference
from app.agents.common.reference import (
    AllergenGroup,
    allergen_groups,
    allergen_terms,
    chronic_restriction_codes,
    chronic_restriction_terms,
    food_safety_terms,
    hazard_terms,
    load_reference,
    parse_allergen_groups,
    parse_food_safety_terms,
)
from app.rules.age import first_month_of
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.term_match import Term, normalize


def test_allergen_terms_key_set_matches_allergen_names() -> None:
    terms = allergen_terms()

    assert {term.key for term in terms} == {str(code) for code in ALLERGEN_NAMES}


def test_allergen_terms_every_label_is_in_its_own_aliases() -> None:
    data = load_reference("allergen_terms.yaml")

    for row in data["terms"]:
        assert row["label"] in row["aliases"], f"code {row['code']} label 이 aliases 에 없다"


def test_allergen_terms_is_cached_same_object() -> None:
    assert allergen_terms() is allergen_terms()


def test_allergen_terms_guard_that_covers_no_alias_raises(tmp_path, monkeypatch) -> None:
    """아무 별칭도 덮지 않는 guard 는 오타이거나 다른 코드에 붙은 것이다 (#258)."""
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "allergen_terms.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-10-07\n"
        "terms:\n  - code: 6\n    label: 밀\n    aliases: [밀]\n    guards: [우유]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="자기 별칭을 덮지 않는다"):
        reference.allergen_terms.__wrapped__()


# ── allergen_terms.yaml groups — 묶음 이름 (#258) ───────────────────────────

_TERMS = (
    Term(key="5", aliases=("대두", "콩"), guards=("강낭콩",)),
    Term(key="8", aliases=("게", "꽃게")),
    Term(key="9", aliases=("새우",)),
    Term(key="18", aliases=("조개",)),
)


def _group(label: str, **raw) -> dict:
    return {
        "label": label,
        "aliases": [label],
        "codes": [],
        "includes": [],
        "guards": [],
        "source": "출처",
        **raw,
    }


def test_real_groups_unfold_includes() -> None:
    groups = {group.label: group for group in allergen_groups()}

    assert groups["갑각류"].codes == {8, 9}
    assert groups["해산물"].codes == {7, 8, 9, 17, 18}
    assert groups["해산물"].members == ("해산물", "생선", "갑각류", "연체류")
    assert groups["콩류"].codes == {4, 5}
    assert allergen_groups() is allergen_groups()


def test_parse_allergen_groups_unfolds_codes_and_members() -> None:
    data = {
        "groups": [
            _group("갑각류", codes=[8, 9]),
            _group("해산물", codes=[18], includes=["갑각류"]),
        ]
    }

    groups = parse_allergen_groups(data, _TERMS)

    assert groups[1] == AllergenGroup(
        label="해산물",
        aliases=("해산물",),
        codes=frozenset({8, 9, 18}),
        members=("해산물", "갑각류"),
        guards=(),
    )


def test_parse_allergen_groups_name_that_is_also_an_alias_must_cover_its_code() -> None:
    # "콩" 묶음이 대두(5)를 빼면 "콩" 으로 등록한 아이가 "대두" 로 등록한 아이보다 덜 막힌다
    good = {"groups": [_group("콩", codes=[5])]}
    bad = {"groups": [_group("콩", codes=[4])]}

    assert parse_allergen_groups(good, _TERMS)[0].codes == {5}
    with pytest.raises(ValueError, match="그 코드를 포함하지 않는다"):
        parse_allergen_groups(bad, _TERMS)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ([_group("갑각류", codes=[8], foods=["가재"])], "정해지지 않은 칸"),
        ([{"aliases": ["갑각류"], "codes": [8]}], "label 이 없다"),
        ([_group("갑각류", codes=[8]), _group("갑각류", codes=[9])], "묶음 label 이 겹친다"),
        ([_group("갑각류", codes=[8], aliases=["crustacean"])], "label 자신이 없다"),
        ([_group("갑각류", codes=[8, 20])], "19종 밖"),
        ([_group("해산물", includes=["어류"])], "없는 묶음"),
        ([_group("가", codes=[8], includes=["나"]), _group("나", includes=["가"])], "돌아온다"),
        ([_group("갑각류")], "codes 가 없다"),
        (
            [
                _group("갑각류", codes=[8]),
                _group("해산물", codes=[18], aliases=["해산물", "갑각류"]),
            ],
            "묶음 이름이 겹친다",
        ),
        ([_group("갑각류", codes=[8], guards=["코코넛"])], "자기 별칭을 덮지 않는다"),
        # 묶음 구성은 의료 검수 대신 근거를 적어 둔다
        ([_group("갑각류", codes=[8], source=" ")], "source"),
        ([{k: v for k, v in _group("갑각류", codes=[8]).items() if k != "source"}], "source"),
    ],
)
def test_parse_allergen_groups_broken_rows_raise(groups: list, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_allergen_groups({"groups": groups}, _TERMS)


# ── food_safety_terms.yaml — Food 전용 음식 어휘 (#258) ───────────────────────

_GROUPS = (AllergenGroup("갑각류", ("갑각류",), frozenset({8, 9}), ("갑각류",), ()),)

FOOD_VALID = {
    "source": "테스트",
    "version": "1",
    "fetched_at": "2026-10-07",
    "code_foods": [{"code": 18, "foods": ["굴", "소라"], "guards": ["소라빵"]}],
    "varies_foods": [{"code": 8, "foods": ["맛살"], "guards": ["치맛살"], "source": "출처"}],
    "extra_allergens": [{"label": "참깨", "aliases": ["참깨", "참기름"], "guards": []}],
    "group_foods": [{"group": "갑각류", "foods": ["가재"], "guards": []}],
    "age_rules": [
        {
            "label": "꿀",
            "below_month": 12,
            "action": "block",
            "aliases": ["꿀"],
            "guards": [],
            "exact": [],
            "source": "출처",
        },
        {
            "label": "카페인",
            "below_month": None,
            "action": "block",
            "scope": "name",
            "aliases": ["커피"],
            "guards": [],
            "exact": ["커피 우유"],
            "ends": ["커피"],
            "source": "출처",
        },
        {
            "label": "떡",
            "below_month": 48,
            "action": "block",
            "daycare_action": "caution",
            "aliases": ["떡"],
            "guards": ["떡갈비"],
            "exact": [],
            "source": "출처",
        },
    ],
}


def food_broken(change) -> dict:
    data = copy.deepcopy(FOOD_VALID)
    change(data)
    return data


def test_parse_food_safety_terms_reads_every_section() -> None:
    food = parse_food_safety_terms(FOOD_VALID, _TERMS, _GROUPS)

    assert food.code_terms == (Term(key="18", aliases=("굴", "소라"), guards=("소라빵",)),)
    assert food.varies_terms == (Term(key="8", aliases=("맛살",), guards=("치맛살",)),)
    assert food.extras == (Term(key="참깨", aliases=("참깨", "참기름")),)
    assert food.group_foods == {"갑각류": Term(key="갑각류", aliases=("가재",))}
    honey, caffeine, rice_cake = food.age_rules
    assert (honey.applies_at(11), honey.applies_at(12)) == (True, False)
    assert caffeine.applies_at(72)
    assert caffeine.exact == {"커피우유"}
    assert caffeine.ends == {"커피"}
    assert (honey.scope, caffeine.scope) == ("all", "name")
    assert (honey.daycare_action, rice_cake.daycare_action) == (None, "caution")


def test_real_food_safety_terms_loads_and_is_cached() -> None:
    food = food_safety_terms()

    assert food is food_safety_terms()
    assert {rule.term.key for rule in food.age_rules} >= {"꿀", "생우유", "질식 주의 식품"}
    assert set(food.group_foods) <= {group.label for group in allergen_groups()}


def test_확인_필요로_옮긴_음식은_이것뿐이다() -> None:
    """varies_foods 는 나중에 확인 질문으로 풀 범위를 정하는 칸이다.

    목록이 바뀌면 이 테스트도 같이 고친다 — 풀릴 범위가 넓어지는 변경이 리뷰에서 보이게 한다.
    """
    food = food_safety_terms()
    varies = {(term.key, name) for term in food.varies_terms for name in term.aliases}

    assert varies == {
        ("6", "간장"),
        ("6", "된장"),
        ("6", "고추장"),
        ("6", "쌈장"),
        ("6", "어묵"),
        ("6", "오뎅"),
        ("6", "카레"),
        ("8", "크래미"),
        ("8", "맛살"),
    }
    wheat = next(term for term in food.code_terms if term.key == "6")
    assert {"짜장", "자장", "춘장"} <= set(wheat.aliases)


def test_age_rule_months_match_their_single_source() -> None:
    """age_gates.yaml 이 아직 없어 월령이 여기에도 적혀 있다. 원래 자리와 어긋나면 깨진다.

    꿀 · 마시는 우유는 영아 단계(toddler 전)와 같은 경계, 질식은 Activity food_choking 축과
    같은 값이다.
    """
    rules = {rule.term.key: rule for rule in food_safety_terms().age_rules}
    toddler = first_month_of("toddler")
    choking = hazard_terms().axes["food_choking"].warn_below_month

    assert rules["꿀"].below_month == rules["생우유"].below_month == toddler
    assert (
        rules["질식 위험 식품"].below_month
        == rules["질식 주의 식품"].below_month
        == rules["떡"].below_month
        == choking
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(age_rule=[]), "정해지지 않은 칸"),
        (lambda d: d["code_foods"][0].update(code=20), "19종 밖"),
        (lambda d: d["code_foods"].append(dict(d["code_foods"][0])), "두 번 있다"),
        (lambda d: d["code_foods"][0].update(foods=[]), "foods 가 비어"),
        (lambda d: d["code_foods"][0].update(guards=["빵"]), "자기 별칭을 덮지 않는다"),
        # 별칭과 똑같은 guard 는 그 별칭을 통째로 끈다 — "꿀" guard 면 꿀 규칙이 사라진다
        (lambda d: d["code_foods"][0].update(guards=["소라"]), "별칭과 같다"),
        (lambda d: d["age_rules"][0].update(guards=["꿀"]), "별칭과 같다"),
        (lambda d: d["extra_allergens"][0].update(label="깨"), "label 자신이 없다"),
        (lambda d: d["extra_allergens"][0].update(aliases=["참깨", "새우"]), "공용 이름 사전"),
        (lambda d: d["group_foods"][0].update(group="견과류"), "없는 묶음"),
        (lambda d: d["age_rules"][0].update(below_month=0), "below_month"),
        (lambda d: d["age_rules"][0].update(below_month=True), "below_month"),
        (lambda d: d["age_rules"][0].update(action="warn"), "action"),
        (lambda d: d["age_rules"][0].update(daycare_action="warn"), "daycare_action"),
        (lambda d: d["age_rules"][0].update(scope="ingredients"), "scope"),
        (lambda d: d["age_rules"][0].update(source=" "), "source"),
        (lambda d: d["age_rules"].append(dict(d["age_rules"][0])), "두 번 있다"),
        (lambda d: d["varies_foods"][0].update(code=20), "19종 밖"),
        (lambda d: d["varies_foods"][0].update(foods=[]), "foods 가 비어"),
        (lambda d: d["varies_foods"][0].update(source=" "), "source"),
        (lambda d: d["varies_foods"][0].pop("source"), "source"),
        (lambda d: d["varies_foods"][0].update(note="x"), "정해지지 않은 칸"),
        (lambda d: d["varies_foods"][0].update(guards=["빵"]), "자기 별칭을 덮지 않는다"),
        # 같은 코드의 확실한 이름(공용 사전 · code_foods)에 있으면 어느 쪽인지 모른다
        (lambda d: d["varies_foods"][0].update(foods=["꽃게"]), "확실한 성분"),
        (lambda d: d["varies_foods"][0].update(code=18, foods=["소라"], guards=[]), "확실한 성분"),
        (lambda d: d["varies_foods"].append(dict(d["varies_foods"][0])), "두 번 있다"),
    ],
)
def test_parse_food_safety_terms_broken_data_raises(change, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_food_safety_terms(food_broken(change), _TERMS, _GROUPS)


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


def test_chronic_restriction_codes_block_the_whole_allergen() -> None:
    """foods 목록에 없는 같은 성분 음식(버터 · 소면)도 막으려고 질환에 코드를 단다 (#258)."""
    codes = chronic_restriction_codes()

    assert codes[normalize("유당 불내증")] == {2}
    assert codes[normalize("갈락토스혈증")] == {2}
    assert codes[normalize("셀리악병")] == {6}


def test_celiac_foods_cover_every_gluten_group_food() -> None:
    """셀리악병 매핑과 "글루텐" 묶음이 갈라지면 같은 아이가 등록 방법에 따라 덜 막힌다."""
    celiac = chronic_restriction_terms()[normalize("셀리악병")][0]

    assert set(food_safety_terms().group_foods["글루텐"].aliases) <= set(celiac.aliases)


def test_allergen_terms_guard_equal_to_an_alias_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "allergen_terms.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-10-07\n"
        "terms:\n  - code: 6\n    label: 밀\n    aliases: [밀, 밀가루]\n    guards: [밀가루]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="별칭과 같다"):
        reference.allergen_terms.__wrapped__()


@pytest.mark.parametrize(
    ("guards", "message"),
    [("[우유]", "자기 별칭을 덮지 않는다"), ("[빵]", "별칭과 같다")],
)
def test_chronic_restriction_terms_checks_guards(tmp_path, monkeypatch, guards, message) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "chronic_restriction.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-10-07\n"
        f"restrictions:\n  - labels: [셀리악병]\n    foods: [밀, 빵]\n    guards: {guards}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        reference.chronic_restriction_terms.__wrapped__()


def test_chronic_restriction_codes_out_of_range_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    (tmp_path / "chronic_restriction.yaml").write_text(
        "source: 테스트\nversion: '1'\nfetched_at: 2026-10-07\n"
        "restrictions:\n  - labels: [유당불내증]\n    codes: [20]\n    foods: [우유]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="19종 밖"):
        reference.chronic_restriction_codes.__wrapped__()


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
