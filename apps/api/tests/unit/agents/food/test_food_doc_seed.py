"""food_doc 시드 — 틀린 모양은 읽을 때 막히고, 적재 검사는 틀린 행을 잡는다 (RAG_plan §6 · #337).

Growth 시드 검사(`test_growth_doc_seed.py`)와 같은 모양으로 두 곳에서 건다.

- 시드 전체에 거는 검사 — 행이 들어오는 순간부터 CI 가 막는다
- 같은 검사를 일부러 틀린 행에 걸어 보는 검사 — 시드가 비어 있어도 검사가 살아 있다는 증거

원문과의 문장 겹침(8어절)은 원문이 저장소 밖이라 테스트로 못 본다. 검수자가 본다.
"""

import re
from collections.abc import Sequence
from datetime import date

import pytest

from app.agents.common.reference import food_safety_terms
from app.agents.common.reference_docs import DOC_MONTH_LIMIT
from app.agents.food.doc_seed import (
    FOOD_GROUPS,
    ROW_TYPES,
    TEXTURES,
    WEANING_TYPES,
    FoodDocEntry,
    allergen_codes_in,
    food_doc_seed,
    parse_food_doc,
)
from app.rules.evaluative import find_evaluative
from app.rules.term_match import TermMatcher, prepare_fields

SEED = food_doc_seed()

# 행 출처로 쓸 수 있는 곳과 그 license_basis (#337). 미국 연방기관 자료는 대조용이라 여기 없다.
# 뉴질랜드 지침은 CC BY 4.0 인데 값 집합에 CC BY 가 없어서 fact_rewrite 로 적는다
ALLOWED_SOURCES = {
    "Ministry of Health (New Zealand)": "fact_rewrite",
    "한국소비자원": "kogl_1",
    "교육부": "public_law",
}

WEANING_START, WEANING_END = 4, 12  # 이유기 행의 월령 — 4개월 이상 12개월 미만
TODDLER_START = 12  # meal_pattern · nutrient_note 는 돌부터 (RAG_plan §3)


def raw(**overrides) -> dict:
    """틀린 데가 없는 시드 한 행(6–7개월 질감). 테스트마다 한두 칸만 바꾼다."""
    row = {
        "doc_key": "food.weaning.texture.m6_7",
        "row_type": "weaning_stage",
        "title": "6~7개월 이유식 질감",
        "body": "숟가락으로 떠먹이는 곱게 간 음식에서 시작해 몇 주에 걸쳐 으깬 음식으로 옮겨 간다.",
        "min_month": 6,
        "max_month": 8,
        "tags": ["weaning", "texture"],
        "source_title": "(테스트) 문서명",
        "source_org": "Ministry of Health (New Zealand)",
        "source_year": 2021,
        "source_locator": "(테스트) 쪽",
        "license_basis": "fact_rewrite",
        "status": "draft",
        "authored_by": "writer-a",
        "version": 1,
        "texture": "mashed",
        "food_groups": [],
        "allergen_codes": [],
    }
    return {**row, **overrides}


def entries_of(*rows: dict) -> tuple[FoodDocEntry, ...]:
    return parse_food_doc({"rows": list(rows)})


def stage(key: str, lo: int, hi: int, **overrides) -> dict:
    return raw(doc_key=key, min_month=lo, max_month=hi, **overrides)


# ── 적재 검사 ────────────────────────────────────────────────────────────────

_MILESTONE = re.compile(
    r"이\s*(?:나이|시기|월령)에는"
    r"|\d+\s*개월\s*(?:쯤|경|무렵)?\s*(?:이면|이\s*되면)"
    r"|(?:대부분의|보통의)\s*(?:아이|아기)"
    r"|(?:아이|아기)들은\s*보통"
    r"|할\s*수\s*있게\s*(?:된다|돼요|됩니다)"
    r"|혼자\s*앉|목을\s*가누|체중|몸무게"
)
_NUTRITION_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:kcal|kg|mg|ml|cc|g(?![a-z])|%|칼로리|킬로그램|밀리그램|그램|밀리리터|퍼센트)",
    re.IGNORECASE,
)
_PRESCRIPTION = re.compile(r"보충제|영양제|처방|진단|복용|투약")


def _texts(entry: FoodDocEntry) -> tuple[str, str]:
    return (entry.meta.title, entry.meta.body)


def missing_codes(entry: FoodDocEntry) -> frozenset[int]:
    """본문에 나오는데 allergen_codes 에 없는 19종 코드."""
    return allergen_codes_in(entry) - entry.allergen_codes


def age_hits(entry: FoodDocEntry) -> tuple[str, ...]:
    """그 행의 가장 어린 월령에서 막히는 연령 규칙 label. 주의(caution)는 보지 않는다 —
    "포도는 4등분" 같은 질식 예방 행을 쓰려면 식품 이름이 나와야 한다 (#337)."""
    rules = [
        rule.term
        for rule in food_safety_terms().age_rules
        if rule.action == "block" and rule.applies_at(entry.meta.min_month)
    ]
    return TermMatcher(rules).match(prepare_fields(_texts(entry)))


def type_problems(entry: FoodDocEntry) -> list[str]:
    """row_type 별로 정해진 칸 · 월령 (own_table §4 · RAG_plan §3)."""
    meta = entry.meta
    problems = []
    if meta.row_type in WEANING_TYPES:
        if not (WEANING_START <= meta.min_month and meta.max_month <= WEANING_END):
            problems.append("weaning_range")
    elif entry.texture is not None:
        problems.append("texture_outside_weaning")
    if meta.row_type in {"meal_pattern", "nutrient_note"} and meta.min_month < TODDLER_START:
        problems.append("toddler_range")
    if meta.row_type == "nutrient_note" and not entry.nutrients:
        problems.append("nutrient_note_nutrients")
    if meta.row_type != "nutrient_note" and entry.nutrients:
        problems.append("nutrients_outside_note")
    return problems


def lint_entry(entry: FoodDocEntry) -> list[str]:
    problems = []
    if missing_codes(entry):
        problems.append("allergen_codes")
    if age_hits(entry):
        problems.append("age_rule")
    texts = _texts(entry)
    if any(find_evaluative(text) for text in texts):
        problems.append("evaluative")
    if any(_MILESTONE.search(text) for text in texts):
        problems.append("milestone")
    if any(_NUTRITION_NUMBER.search(text) for text in texts):
        problems.append("nutrition_number")
    if any(_PRESCRIPTION.search(text) for text in texts):
        problems.append("prescription")
    if ALLOWED_SOURCES.get(entry.meta.source_org) != entry.meta.license_basis:
        problems.append("source")
    return problems + type_problems(entry)


def uncovered_months(entries: Sequence[FoodDocEntry], *, approved_only: bool) -> list[int]:
    """이유기(4–11개월) 중 weaning_stage 행이 하나도 덮지 않는 월령. retired 는 세지 않는다."""
    stages = [
        e
        for e in entries
        if e.meta.row_type == "weaning_stage"
        and e.meta.status != "retired"
        and (e.meta.status == "approved" or not approved_only)
    ]
    return [
        months
        for months in range(WEANING_START, WEANING_END)
        if not any(e.meta.covers(months) for e in stages)
    ]


# ── 시드 전체 ────────────────────────────────────────────────────────────────


def test_시드_파일을_읽는다():
    assert isinstance(SEED, tuple)


def test_시드의_검수_전_행도_로더가_읽는다():
    """검수 전(draft) 행도 읽혀야 적재 검사가 그 행을 본다.
    검수를 건너뛰고 approved 로 올리지 않는다 — approved 행은 검수자 · 검수일이 있어야
    읽힌다 (parse_doc_meta)."""
    drafts = [e for e in SEED if e.meta.status == "draft"]
    if not drafts:
        pytest.skip("draft 행이 없다 — 전부 검수를 마쳤다")
    assert all(e.meta.reviewed_at is None for e in drafts)


def test_시드_모든_행이_적재_검사를_통과한다():
    failed = {e.meta.doc_key: lint_entry(e) for e in SEED if lint_entry(e)}
    assert failed == {}


def test_시드에_이유식_단계가_있으면_4_11개월을_빈틈없이_덮는다():
    """검수 전(draft) 행까지 센다. approved 만으로 덮는지는 검수 때 본다."""
    if not any(e.meta.row_type == "weaning_stage" for e in SEED):
        pytest.skip("weaning_stage 행이 아직 없다")
    assert uncovered_months(SEED, approved_only=False) == []


# ── 적재 검사가 틀린 행을 잡는다 ──────────────────────────────────────────────


def test_멀쩡한_행은_통과한다():
    (entry,) = entries_of(raw())
    assert lint_entry(entry) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"body": "달걀노른자를 으깨어 섞는다."}, "allergen_codes"),
        ({"title": "두부 으깨기"}, "allergen_codes"),
        ({"body": "꿀을 조금 섞으면 잘 먹는다."}, "age_rule"),
        ({"body": "또래보다 빠르게 덩어리를 먹도록 돕는다."}, "evaluative"),
        ({"body": "혼자 앉을 수 있으면 시작한다."}, "milestone"),
        ({"body": "출생 체중의 두 배가 되면 시작한다."}, "milestone"),
        ({"body": "8개월이 되면 다진 음식을 먹는다."}, "milestone"),
        ({"body": "대부분의 아기는 손으로 집어 먹는다."}, "milestone"),
        ({"body": "한 번에 30g 정도 준다."}, "nutrition_number"),
        ({"body": "하루 500ml 를 넘기지 않는다."}, "nutrition_number"),
        ({"body": "철분 보충제를 함께 먹인다."}, "prescription"),
        ({"source_org": "CDC"}, "source"),
        ({"license_basis": "kogl_1"}, "source"),
    ],
)
def test_적재_검사가_틀린_행을_잡는다(overrides, expected):
    (entry,) = entries_of(raw(**overrides))
    assert expected in lint_entry(entry)


def test_적힌_코드가_본문을_덮으면_통과한다():
    (entry,) = entries_of(raw(body="달걀노른자를 으깨어 섞는다.", allergen_codes=[1]))
    assert "allergen_codes" not in lint_entry(entry)


def test_제품마다_다른_성분도_코드로_적어야_한다():
    """간장은 제품에 따라 밀 · 대두가 들어간다. 문서 행은 확인 질문을 붙일 자리가 없다."""
    (entry,) = entries_of(raw(row_type="feeding_tip", texture=None, body="간장을 넣지 않는다."))
    assert {5, 6} <= allergen_codes_in(entry)


def _codes_of(body: str) -> frozenset[int]:
    (entry,) = entries_of(raw(row_type="feeding_tip", texture=None, body=body))
    return allergen_codes_in(entry)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("꽃게를 쪄서 살을 발라 준다.", 8),
        ("꽃게 살을 발라 준다.", 8),
        ("게살을 잘게 찢는다.", 8),
        ("대게", 8),
        ("밀가루 반죽", 6),
        ("통밀빵을 손가락 모양으로 자른다.", 6),
        ("식빵을 준다.", 6),
        ("콩국수", 5),
        ("콩을 삶는다.", 5),
        ("새우를 다진다.", 9),
        ("한치를 데친다.", 17),
    ],
)
def test_문장용_가림은_음식_이름을_놓치지_않는다(body, code):
    assert code in _codes_of(body)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("손으로 집어 입에 가져가 보게 한다.", 8),  # 어미 -게
        ("익숙해지게 한다.", 8),
        ("게다가 맛도 다양하다.", 8),
        ("그릇을 밀어내는 것은 배부르다는 뜻이다.", 6),
        ("밀폐 용기에 담는다.", 6),
        ("숟가락을 아기 쪽으로 내밀고 기다린다.", 6),
        ("콩팥이 덜 자랐다.", 5),
        ("먹는 데 집중하게 한다.", 9),  # 집중하 — 중하(새우)
        ("살균한 치즈를 준다.", 17),  # 한 + 치즈 — 띄어쓰기를 넘어 붙지 않는다
    ],
)
def test_문장의_문법은_알레르기_코드로_읽지_않는다(body, code):
    assert code not in _codes_of(body)


def test_질식_주의_식품은_막지_않는다():
    """caution 은 통과 — 질식 예방 행에 식품 이름이 나와야 한다 (#337)."""
    (entry,) = entries_of(
        raw(
            doc_key="food.tip.choking.round",
            row_type="feeding_tip",
            texture=None,
            body="포도와 방울토마토는 세로로 4등분해서 준다.",
            min_month=8,
            max_month=DOC_MONTH_LIMIT,
            allergen_codes=[12],  # 방울토마토 → 토마토
        )
    )
    assert lint_entry(entry) == []


def test_연령_규칙은_행의_가장_어린_월령에서_본다():
    """돌이 지난 행에서는 꿀이 막히지 않는다."""
    (entry,) = entries_of(
        raw(
            row_type="feeding_tip",
            texture=None,
            body="꿀은 아주 조금만 쓴다.",
            min_month=12,
            max_month=36,
        )
    )
    assert age_hits(entry) == ()
    (young,) = entries_of(raw(row_type="feeding_tip", texture=None, body="꿀은 아주 조금만 쓴다."))
    assert age_hits(young) == ("꿀",)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"min_month": 2, "max_month": 6}, "weaning_range"),
        ({"min_month": 10, "max_month": 14}, "weaning_range"),
        (
            {"row_type": "feeding_tip", "min_month": 12, "max_month": 36},
            "texture_outside_weaning",
        ),
        (
            {"row_type": "meal_pattern", "texture": None, "min_month": 9, "max_month": 36},
            "toddler_range",
        ),
        (
            {"row_type": "nutrient_note", "texture": None, "min_month": 12, "max_month": 36},
            "nutrient_note_nutrients",
        ),
        ({"nutrients": ["iron"]}, "nutrients_outside_note"),
    ],
)
def test_row_type_별_칸_검사가_틀린_행을_잡는다(overrides, expected):
    (entry,) = entries_of(raw(**overrides))
    assert expected in type_problems(entry)


def test_평가_표현은_제목에서도_잡는다():
    (entry,) = entries_of(raw(title="또래보다 빠른 이유식"))
    assert "evaluative" in lint_entry(entry)


class TestCoverage:
    def test_세_단계가_이어지면_빈틈이_없다(self):
        rows = [
            stage("food.weaning.a", 4, 6),
            stage("food.weaning.b", 6, 8),
            stage("food.weaning.c", 8, 12),
        ]
        assert uncovered_months(entries_of(*rows), approved_only=False) == []

    def test_빈_월령을_돌려준다(self):
        rows = [stage("food.weaning.a", 4, 6), stage("food.weaning.c", 8, 12)]
        assert uncovered_months(entries_of(*rows), approved_only=False) == [6, 7]

    def test_approved_만_볼_때는_검수_전_행이_덮지_않는다(self):
        approved = {
            "status": "approved",
            "reviewed_by": "reviewer-b",
            "reviewed_at": date(2026, 10, 12),
        }
        rows = [stage("food.weaning.a", 4, 8, **approved), stage("food.weaning.b", 8, 12)]
        entries = entries_of(*rows)
        assert uncovered_months(entries, approved_only=False) == []
        assert uncovered_months(entries, approved_only=True) == [8, 9, 10, 11]


# ── 읽기 ────────────────────────────────────────────────────────────────────


class TestParse:
    def test_rows_칸이_없으면_실패한다(self):
        with pytest.raises(ValueError, match="rows"):
            parse_food_doc({})

    def test_행이_아직_없는_시드는_읽힌다(self):
        assert parse_food_doc({"rows": []}) == ()

    def test_draft_행은_검수_칸_없이도_읽힌다(self):
        (entry,) = entries_of(raw())
        assert entry.meta.status == "draft"
        assert (entry.meta.reviewed_by, entry.meta.reviewed_at) == (None, None)

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("texture", "chunky", "texture"),
            ("food_groups", ["protein"], "food_groups"),
            ("food_groups", "grains", "food_groups"),
            ("nutrients", "iron", "nutrients"),
            ("allergen_codes", [20], "allergen_codes"),
            ("allergen_codes", ["1"], "allergen_codes"),
            ("allergen_codes", [True], "allergen_codes"),
        ],
    )
    def test_도메인_칸이_깨지면_실패한다(self, field, value, message):
        with pytest.raises(ValueError, match=message):
            entries_of(raw(**{field: value}))

    def test_정해지지_않은_칸은_실패한다(self):
        with pytest.raises(ValueError, match="정해지지 않은 칸"):
            entries_of(raw(search_text="손으로 적지 않는다"))

    def test_doc_key_는_food_로_시작한다(self):
        with pytest.raises(ValueError, match="food."):
            entries_of(raw(doc_key="growth.weaning.x"))

    def test_doc_key_가_겹치면_실패한다(self):
        with pytest.raises(ValueError, match="겹친다"):
            entries_of(raw(), raw())

    def test_approved_는_검수자가_있어야_한다(self):
        """작성자 ≠ 검수자 · approved 의 검수 칸은 공통 검사(parse_doc_meta)가 막는다."""
        with pytest.raises(ValueError, match="검수자"):
            entries_of(raw(status="approved"))
        with pytest.raises(ValueError, match="같다"):
            entries_of(raw(reviewed_by="writer-a"))

    def test_값_집합(self):
        assert ROW_TYPES == {
            "weaning_stage",
            "weaning_ingredient",
            "meal_pattern",
            "nutrient_note",
            "feeding_tip",
        }
        assert TEXTURES == {"puree", "mashed", "minced", "soft_pieces", "regular"}
        assert len(FOOD_GROUPS) == 6
