"""growth_doc 시드 — 틀린 모양은 읽을 때 막히고, 적재 검사는 틀린 행을 잡는다 (RAG_plan §6).

`reference/growth_doc.yaml` 의 P0 행은 #293 에서 채웠고, 남은 row_type 은 뒤에 들어온다.
그래서 행이 없는 row_type 도 검사에 이가 있는지 보도록 두 곳에서 건다.

- 시드 전체에 거는 검사 — 행이 들어오는 순간부터 CI 가 막는다
- 같은 검사를 일부러 틀린 행에 걸어 보는 검사 — 시드가 비어 있어도 검사가 살아 있다는 증거

원문과의 문장 겹침(8어절)은 원문이 저장소 밖이라 테스트로 못 본다. 검수자가 본다.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import get_args

import pytest

from app.agents.common.reference import hazard_terms
from app.agents.common.reference_docs import DOC_MONTH_LIMIT
from app.agents.growth.doc_seed import (
    AREAS,
    ROUTINE_CATEGORIES,
    ROW_TYPES,
    GrowthDocEntry,
    chain_key,
    doc_id,
    growth_doc_seed,
    parse_growth_doc,
)
from app.agents.growth.gating import EDUCATION_MIN_MONTH, HABIT_MIN_MONTH, MANNER_MIN_MONTH
from app.agents.growth.store.inmemory import InMemoryGrowthDocs
from app.agents.growth.store.ports import GrowthDocType
from app.agents.growth.tools.routine import ChainError, pick_next_step
from app.agents.growth.tools.safety import load_safety_rules
from app.agents.growth.tools.triggers import trigger_tag_set
from app.agents.memory.schemas.common import RoutineCategory
from app.rules.evaluative import find_evaluative
from app.rules.term_match import match_terms

SEED = growth_doc_seed()
RULES = load_safety_rules()
TRIGGER_TAGS = trigger_tag_set()

# 이 수의 approved 행이 모이면 월령 구간 빈틈을 CI 가 막기 시작한다.
# 행은 여러 PR 로 나눠 들어오므로 그 전에는 비어 있는 구간이 있어도 정상이다 (plan1013 4번)
P0_LEARNING_ROWS = 60

K1 = "growth.routine.self_care.toothbrush.step1"
K2 = "growth.routine.self_care.toothbrush.step2"
K3 = "growth.routine.self_care.toothbrush.step3"


def raw(**overrides) -> dict:
    """틀린 데가 없는 시드 한 행(양치 1단계). 테스트마다 한두 칸만 바꾼다."""
    row = {
        "doc_key": K1,
        "row_type": "routine_step",
        "title": "양치 — 어른이 칫솔을 쥐고 닦아 주는 단계",
        "body": "아이가 입을 벌리면 어른이 칫솔을 쥐고 안쪽부터 닦아 준다. 노래 한 곡으로 끝낸다.",
        "min_month": 12,
        "max_month": 36,
        "tags": ["self_care", "assistance:full_assist"],
        "source_title": "(테스트) 문서명",
        "source_org": "(테스트) 발행처",
        "source_year": 2020,
        "source_locator": "(테스트) 절",
        "license_basis": "fact_rewrite",
        "status": "draft",
        "authored_by": "writer-a",
        "version": 1,
        "routine_category": "self_care",
        "materials": ["칫솔"],
        "setting": "indoor",
    }
    return {**row, **overrides}


def chain(*, levels=("full_assist", "partial_assist", "independent")) -> list[dict]:
    keys = (K1, K2, K3)
    rows = []
    for i, level in enumerate(levels):
        rows.append(
            raw(
                doc_key=keys[i],
                tags=["self_care", f"assistance:{level}"],
                **({"next_step_of": keys[i - 1]} if i else {}),
            )
        )
    return rows


def entries_of(*rows: dict) -> tuple[GrowthDocEntry, ...]:
    return parse_growth_doc({"rows": list(rows)})


# 예절 · 습관 · 리듬 행의 doc_key. 습관은 셋째 칸이 그 행의 상황 태그다
_NEW_KEYS = {
    "manner_practice": re.compile(r"growth\.manner\.[a-z_]+\.[a-z0-9_]+"),
    "habit_strategy": re.compile(r"growth\.habit\.([a-z_]+)\.[a-z0-9_]+"),
    "rhythm_info": re.compile(r"growth\.rhythm\.m\d+_\d+\.[a-z0-9_]+"),
}
_STRATEGY_BASE = {
    "manner_practice": {
        "doc_key": "growth.manner.greeting.puppet",
        "title": "인사하기 — 인형 둘로 인사 주고받기",
        "body": "어른과 아이가 인형을 하나씩 들고 문 앞에서 만난 것처럼 인사를 주고받는다.",
        "routine_category": "social_manner",
        "min_month": MANNER_MIN_MONTH,
        "max_month": 72,
        "tags": ["social_manner"],
        "materials": ["인형"],
    },
    "habit_strategy": {
        "doc_key": "growth.habit.anxious.name_feeling",
        "title": "긴장할 때 — 마음을 말로 붙여 주기",
        "body": "아이가 긴장해 보이면 어른이 그 마음을 말로 붙여 주고 손을 잡는다.",
        "routine_category": "habit",
        "trigger_tags": ["anxious"],
        "min_month": HABIT_MIN_MONTH,
        "max_month": 72,
        "tags": ["habit"],
        "materials": [],
    },
    "rhythm_info": {
        "doc_key": "growth.rhythm.m6_9.evening_order",
        "title": "저녁 순서 — 같은 순서로 하루 마무리하기",
        "body": "저녁마다 씻기, 옷 갈아입기, 불 끄기를 같은 순서로 하고 하루를 마무리한다.",
        "routine_category": None,
        "min_month": 6,
        "max_month": 9,
        "tags": ["rhythm"],
        "materials": [],
    },
}
NEW_ROW_TYPES = sorted(_STRATEGY_BASE)
# 리듬 안내는 잠 · 하루 일과 순서만 — 수유 · 이유식은 Food 몫이고, 시간 · 횟수 숫자는 기준표라
# 문서 행에 넣지 않는다 (RAG_plan §0-4). 본문이 모델을 거치지 않고 화면에 그대로 나간다
_RHYTHM_FEEDING = re.compile(r"수유|분유|모유|이유식|젖병|젖을|젖 먹")
_RHYTHM_NUMBER = re.compile(r"\d+\s*(?:시간|분|번|회)")


def strategy(row_type: str, **overrides) -> dict:
    """검수 전(draft)인 예절 · 습관 · 리듬 행 하나. 칸 검사를 통과하는 모양이다."""
    return raw(row_type=row_type, **{**_STRATEGY_BASE[row_type], **overrides})


def learning(key: str, lo: int, hi: int, **overrides) -> dict:
    """검수를 마친 학습 활동 한 행."""
    base = {
        "doc_key": key,
        "row_type": "learning_activity",
        "routine_category": None,
        "tags": ["art"],
        "area": "art",
        "min_month": lo,
        "max_month": hi,
        "materials": [],
        "status": "approved",
        "reviewed_by": "reviewer-b",
        "reviewed_at": date(2026, 10, 9),
    }
    return raw(**{**base, **overrides})


# ── 적재 검사 ────────────────────────────────────────────────────────────────

_MILESTONE = re.compile(
    r"이\s*(?:나이|시기|월령)"
    r"|\d+\s*개월\s*(?:쯤|경|무렵)?\s*(?:이면|이\s*되면)"
    r"|(?:대부분의|보통의)\s*(?:아이|아기)"
    r"|(?:아이|아기)들은\s*보통"
    r"|할\s*수\s*있게\s*(?:된다|돼요|됩니다)"
)


def _texts(entry: GrowthDocEntry) -> tuple[str, ...]:
    """문장 · 재료를 따로 대조한다. 이어 붙이면 없는 위험이 생긴다."""
    return (entry.meta.title, entry.meta.body, *entry.materials)


def hazard_hits(entry: GrowthDocEntry) -> list[tuple[str, str]]:
    """그 행의 가장 어린 월령에서 걸리는 (축, 판정). 경고도 막는다 — 예시는 통과하는 모양만."""
    dictionary = hazard_terms()
    hits = []
    for text in _texts(entry):
        for key in match_terms(text, dictionary.terms):
            axis = dictionary.axis_of(key)
            if (level := axis.level_at(entry.meta.min_month)) is not None:
                hits.append((axis.name, level))
    return hits


def type_problems(entry: GrowthDocEntry) -> list[str]:
    """row_type 별로 정해진 칸 · 월령 (own_table §1 · RAG_plan §4 · gating.py)."""
    meta, category = entry.meta, entry.routine_category
    if meta.row_type == "learning_activity":
        problems = []
        if entry.area is None:
            problems.append("learning_area")
        if meta.min_month < EDUCATION_MIN_MONTH:
            problems.append("learning_min_month")
        if meta.min_month < 36 < meta.max_month:  # 12–35 는 표준보육과정, 36+ 는 누리과정
            problems.append("learning_split_36")
        return problems
    if meta.row_type == "routine_step":
        ok = category in {"self_care", "mealtime", "household_task", "transition"}
        return [] if ok and meta.min_month >= EDUCATION_MIN_MONTH else ["step_category_or_month"]
    if meta.row_type == "manner_practice":
        problems = [] if category == "social_manner" else ["manner_category"]
        problems += [] if meta.min_month >= MANNER_MIN_MONTH else ["manner_min_month"]
        problems += [] if _NEW_KEYS["manner_practice"].fullmatch(meta.doc_key) else ["manner_key"]
        # 행 이름(제목의 " — " 앞)을 관찰 subject 와 맞춘다 (tools/docs.strategy_rows)
        return problems + ([] if " — " in meta.title else ["manner_name"])
    if meta.row_type == "habit_strategy":
        problems = [] if category == "habit" else ["habit_category"]
        if not entry.trigger_tags:
            return problems + ["habit_trigger"]
        # 상황 태그는 사전(reference/growth_triggers.yaml)의 것 하나 — 사전에 없는 태그의 행은
        # 코드가 고르지 못한다
        if len(entry.trigger_tags) != 1 or entry.trigger_tags[0] not in TRIGGER_TAGS:
            problems.append("habit_trigger_tag")
        key = _NEW_KEYS["habit_strategy"].fullmatch(meta.doc_key)
        if key is None or key.group(1) not in entry.trigger_tags:
            problems.append("habit_key")
        return problems
    if meta.row_type == "rhythm_info":
        problems = [] if meta.max_month <= EDUCATION_MIN_MONTH else ["rhythm_range"]
        problems += [] if _NEW_KEYS["rhythm_info"].fullmatch(meta.doc_key) else ["rhythm_key"]
        texts = (meta.title, meta.body)
        problems += ["rhythm_feeding"] if any(_RHYTHM_FEEDING.search(t) for t in texts) else []
        return problems + (
            ["rhythm_number"] if any(_RHYTHM_NUMBER.search(t) for t in texts) else []
        )
    return []


def lint_entry(entry: GrowthDocEntry) -> list[str]:
    problems = []
    if any(find_evaluative(text) for text in _texts(entry)):
        problems.append("evaluative")
    if hazard_hits(entry):
        problems.append("hazard")
    if any(RULES.food_terms(text) for text in _texts(entry)):
        problems.append("food_term")
    if any(_MILESTONE.search(text) for text in (entry.meta.title, entry.meta.body)):
        problems.append("milestone")
    return problems + type_problems(entry)


def chain_problems(entries: Sequence[GrowthDocEntry]) -> list[str]:
    """사슬 이름(`chain_key`)마다 `pick_next_step` 이 읽을 수 있는 한 줄인지 본다.

    같은 함수(`ordered_chain` · 도움 수준 태그 검사)를 그대로 쓴다 — run 과 다른 눈으로 보지 않게.
    두 번 본다. 쓰고 있는 행 전부(retired 빼고)와, run 이 실제로 읽는 approved 행만.
    중간 단계만 검수가 안 끝났으면 approved 쪽에서 사슬이 끊긴다.
    """
    live = [e for e in entries if e.meta.row_type == "routine_step" and e.meta.status != "retired"]
    approved = [e for e in live if e.meta.status == "approved"]
    problems = []
    for view, members in (("전체", live), ("approved", approved)):
        groups: dict[str, list[GrowthDocEntry]] = defaultdict(list)
        for entry in members:
            groups[chain_key(entry.meta.doc_key)].append(entry)
        for key, chain_rows in groups.items():
            try:
                pick_next_step([e.to_row() for e in chain_rows], assistance_level="full_assist")
            except ChainError as exc:
                problems.append(f"{key} ({view}): {exc}")
    return problems


def uncovered_months(entries: Sequence[GrowthDocEntry], row_type: str) -> list[int]:
    """교육 tool 이 열리는 월령부터 v1 끝까지, 검수를 마친 행이 하나도 덮지 않는 월령."""
    approved = [e for e in entries if e.meta.row_type == row_type and e.meta.status == "approved"]
    return [
        months
        for months in range(EDUCATION_MIN_MONTH, DOC_MONTH_LIMIT)
        if not any(e.meta.covers(months) for e in approved)
    ]


# ── 시드 전체 ────────────────────────────────────────────────────────────────


def test_시드_파일을_읽는다():
    assert isinstance(SEED, tuple)


def test_시드_모든_행이_적재_검사를_통과한다():
    failed = {e.meta.doc_key: lint_entry(e) for e in SEED if lint_entry(e)}
    assert failed == {}


def test_시드의_자립_단계_사슬이_끊기지_않는다():
    assert chain_problems(SEED) == []


def test_학습_활동이_모자라지_않을_만큼_모이면_모든_월령이_덮인다():
    approved = [
        e for e in SEED if e.meta.row_type == "learning_activity" and e.meta.status == "approved"
    ]
    if len(approved) < P0_LEARNING_ROWS:
        pytest.skip(
            f"approved 학습 활동 {len(approved)}/{P0_LEARNING_ROWS} — 행 PR 이 다 들어오기 전"
        )
    assert uncovered_months(SEED, "learning_activity") == []


def test_루틴_카테고리는_관찰과_같은_값_집합이다():
    assert ROUTINE_CATEGORIES == {category.value for category in RoutineCategory}


def test_row_type_은_포트의_종류와_같다():
    assert ROW_TYPES == frozenset(get_args(GrowthDocType))


# ── 적재 검사가 틀린 행을 잡는다 ──────────────────────────────────────────────


def test_멀쩡한_행은_통과한다():
    assert [lint_entry(e) for e in entries_of(*chain())] == [[], [], []]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"body": "또래보다 빠르게 칫솔질을 하도록 돕는다."}, "evaluative"),
        ({"materials": ["구슬"]}, "hazard"),
        ({"body": "달걀을 깨뜨려 보게 한다."}, "food_term"),
        ({"materials": ["우유"]}, "food_term"),
        ({"body": "이 나이에는 혼자 칫솔을 쥘 수 있다."}, "milestone"),
        ({"body": "18개월이면 스스로 문지르게 된다."}, "milestone"),
        ({"body": "대부분의 아이는 컵을 두 손으로 쥔다."}, "milestone"),
        (
            {"doc_key": "growth.routine.habit.nail.step1", "routine_category": "habit"},
            "step_category_or_month",
        ),
    ],
)
def test_적재_검사가_틀린_행을_잡는다(overrides, expected):
    (entry,) = entries_of(raw(**overrides))
    assert expected in lint_entry(entry)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"row_type": "learning_activity", "area": None, "routine_category": None},
            "learning_area",
        ),
        (
            {"row_type": "learning_activity", "area": "art", "min_month": 6, "max_month": 12},
            "learning_min_month",
        ),
        (
            {"row_type": "learning_activity", "area": "art", "min_month": 30, "max_month": 40},
            "learning_split_36",
        ),
        ({"row_type": "manner_practice", "routine_category": "self_care"}, "manner_category"),
        ({"row_type": "manner_practice", "routine_category": "social_manner"}, "manner_min_month"),
        (
            {"row_type": "habit_strategy", "min_month": HABIT_MIN_MONTH, "max_month": 60},
            "habit_category",
        ),
        (
            {
                "row_type": "habit_strategy",
                "min_month": HABIT_MIN_MONTH,
                "max_month": 60,
                "routine_category": "habit",
            },
            "habit_trigger",
        ),
        ({"row_type": "rhythm_info", "min_month": 0, "max_month": 24}, "rhythm_range"),
    ],
)
def test_row_type_별_칸_검사가_틀린_행을_잡는다(overrides, expected):
    (entry,) = entries_of(raw(**overrides))
    assert expected in type_problems(entry)


@pytest.mark.parametrize(
    ("row_type", "overrides", "expected"),
    [
        ("manner_practice", {"title": "인형 둘로 인사 주고받기"}, "manner_name"),
        ("manner_practice", {"doc_key": "growth.etiquette.greeting.puppet"}, "manner_key"),
        ("habit_strategy", {"trigger_tags": ["sleepy"]}, "habit_trigger_tag"),
        ("habit_strategy", {"trigger_tags": ["anxious", "tired"]}, "habit_trigger_tag"),
        ("habit_strategy", {"doc_key": "growth.habit.tired.name_feeling"}, "habit_key"),
        ("rhythm_info", {"doc_key": "growth.rhythm.sleep.evening_order"}, "rhythm_key"),
        ("rhythm_info", {"body": "잠들기 전에 수유를 하고 불을 끈다."}, "rhythm_feeding"),
        ("rhythm_info", {"body": "낮잠은 2번, 한 번에 1시간쯤 재운다."}, "rhythm_number"),
    ],
)
def test_예절_습관_리듬_행의_이름_태그_키를_본다(row_type, overrides, expected):
    (entry,) = entries_of(strategy(row_type, **overrides))
    assert expected in type_problems(entry)


class TestNewRowTypes:
    """예절 · 습관 · 리듬 행도 읽을 때 DB CHECK 와 같은 것을 막는다 (own_table §5)."""

    @pytest.mark.parametrize("row_type", NEW_ROW_TYPES)
    def test_멀쩡한_draft_행은_읽히고_적재_검사를_통과한다(self, row_type):
        (entry,) = entries_of(strategy(row_type))
        assert lint_entry(entry) == []

    @pytest.mark.parametrize("row_type", NEW_ROW_TYPES)
    def test_approved_인데_검수자가_없으면_읽을_때_실패한다(self, row_type):
        with pytest.raises(ValueError, match="검수자"):
            entries_of(strategy(row_type, status="approved"))

    @pytest.mark.parametrize("row_type", NEW_ROW_TYPES)
    def test_작성자가_검수하면_읽을_때_실패한다(self, row_type):
        with pytest.raises(ValueError, match="같다"):
            entries_of(
                strategy(
                    row_type,
                    status="approved",
                    reviewed_by="writer-a",
                    reviewed_at=date(2026, 10, 19),
                )
            )

    def test_습관_행은_36개월_전이면_읽을_때_실패한다(self):
        with pytest.raises(ValueError, match=str(HABIT_MIN_MONTH)):
            entries_of(strategy("habit_strategy", min_month=HABIT_MIN_MONTH - 1))

    @pytest.mark.parametrize("row_type", NEW_ROW_TYPES)
    async def test_검수를_마친_행만_포트로_나간다(self, row_type, monkeypatch):
        approved = strategy(
            row_type, status="approved", reviewed_by="reviewer-b", reviewed_at=date(2026, 10, 19)
        )
        draft = strategy(row_type, doc_key=f"{approved['doc_key']}_draft")
        entries = entries_of(approved, draft)
        monkeypatch.setattr("app.agents.growth.store.inmemory.growth_doc_seed", lambda: entries)
        base = _STRATEGY_BASE[row_type]
        rows = await InMemoryGrowthDocs.from_seed().search(
            months=base["min_month"],
            row_type=row_type,
            routine_category=base["routine_category"],
            trigger_tags=tuple(base.get("trigger_tags", ())),
            query="",
            limit=99,
        )
        assert [r.doc_key for r in rows] == [approved["doc_key"]]


def test_평가_표현은_제목에서도_잡는다():
    (entry,) = entries_of(raw(title="또래보다 빠른 양치"))
    assert "evaluative" in lint_entry(entry)


def test_위험_용어는_행의_월령에서_본다():
    """72개월 미만이면 작은 부품은 경고라도 걸린다."""
    (entry,) = entries_of(raw(materials=["구슬"], min_month=48, max_month=72))
    assert hazard_hits(entry) == [("small_parts", "warn")]


class TestChain:
    def test_이어진_사슬은_통과한다(self):
        assert chain_problems(entries_of(*chain())) == []

    def test_사슬_하나짜리도_도움_수준_태그가_있어야_한다(self):
        assert chain_problems(entries_of(raw(tags=["self_care"]))) != []

    def test_갈라지면_잡는다(self):
        rows = [*chain(), raw(doc_key="growth.routine.self_care.toothbrush.step4", next_step_of=K1)]
        assert any("갈라" in p for p in chain_problems(entries_of(*rows)))

    def test_앞_단계를_빠뜨려_끊기면_잡는다(self):
        """같은 사슬 이름인데 처음이 둘이다 — 끊긴 칸이 한 칸짜리 사슬로 보이면 안 된다."""
        rows = chain()
        rows[2] = {k: v for k, v in rows[2].items() if k != "next_step_of"}
        assert any("처음이 2개" in p for p in chain_problems(entries_of(*rows)))

    def test_가운데만_검수가_안_끝나면_run_이_읽는_사슬이_끊긴다(self):
        approved = {
            "status": "approved",
            "reviewed_by": "reviewer-b",
            "reviewed_at": date(2026, 10, 9),
        }
        rows = chain()
        rows[0] = {**rows[0], **approved}
        rows[2] = {**rows[2], **approved}
        problems = chain_problems(entries_of(*rows))
        assert problems and all("(approved)" in p for p in problems)

    def test_사슬_이름이_다르면_다른_사슬이다(self):
        other = raw(doc_key="growth.routine.self_care.handwash.step1")
        assert chain_problems(entries_of(*chain(), other)) == []

    def test_돌면_잡는다(self):
        rows = chain()
        rows[0] = {**rows[0], "next_step_of": K3}
        assert chain_problems(entries_of(*rows)) != []

    def test_도움_수준_태그가_둘이면_잡는다(self):
        rows = chain()
        rows[1] = {**rows[1], "tags": ["assistance:partial_assist", "assistance:independent"]}
        assert chain_problems(entries_of(*rows)) != []

    def test_혼자_하는_쪽에서_도움이_많은_쪽으로_가면_잡는다(self):
        assert (
            chain_problems(
                entries_of(*chain(levels=("independent", "partial_assist", "full_assist")))
            )
            != []
        )

    def test_사슬_안에서_카테고리가_다르면_읽을_때_실패한다(self):
        """사슬 이름에 카테고리가 들어 있어서 한 사슬은 카테고리가 하나다."""
        rows = chain()
        rows[2] = {**rows[2], "routine_category": "mealtime"}
        with pytest.raises(ValueError, match="doc_key"):
            entries_of(*rows)

    def test_다른_사슬의_행을_앞_단계로_가리키면_실패한다(self):
        other = raw(doc_key="growth.routine.self_care.handwash.step2", next_step_of=K1)
        with pytest.raises(ValueError, match="next_step_of"):
            entries_of(raw(), other)

    def test_루틴_단계가_아닌_행은_보지_않는다(self):
        assert chain_problems(entries_of(learning("growth.learn.art.1", 12, 36))) == []


class TestCoverage:
    def test_두_과정이_이어지면_빈틈이_없다(self):
        rows = [
            learning("growth.learn.art.a", 12, 36),
            learning("growth.learn.art.b", 36, DOC_MONTH_LIMIT),
        ]
        assert uncovered_months(entries_of(*rows), "learning_activity") == []

    def test_빈_월령을_돌려준다(self):
        rows = [learning("growth.learn.art.a", 12, 36)]
        assert uncovered_months(entries_of(*rows), "learning_activity") == list(
            range(36, DOC_MONTH_LIMIT)
        )

    def test_검수_전_행은_덮지_않는다(self):
        rows = [
            learning(
                "growth.learn.art.a",
                12,
                DOC_MONTH_LIMIT,
                status="draft",
                reviewed_by=None,
                reviewed_at=None,
            )
        ]
        assert uncovered_months(entries_of(*rows), "learning_activity") == list(
            range(12, DOC_MONTH_LIMIT)
        )


# ── 읽기 ────────────────────────────────────────────────────────────────────


class TestParse:
    def test_rows_칸이_없으면_실패한다(self):
        with pytest.raises(ValueError, match="rows"):
            parse_growth_doc({})

    def test_행이_아직_없는_시드는_읽힌다(self):
        """빈 시드도 읽힌다 — 행이 없는 판에서도 로더가 실패하지 않는다."""
        assert parse_growth_doc({"rows": []}) == ()

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("area", "math", "area"),
            ("routine_category", "sleep", "routine_category"),
            ("setting", "anywhere", "setting"),
            ("materials", "칫솔", "materials"),
            ("trigger_tags", "bored", "trigger_tags"),
            ("next_step_of", "growth.routine.nope", "next_step_of"),
        ],
    )
    def test_도메인_칸이_깨지면_실패한다(self, field, value, message):
        with pytest.raises(ValueError, match=message):
            entries_of(raw(**{field: value}))

    def test_정해지지_않은_칸은_실패한다(self):
        with pytest.raises(ValueError, match="정해지지 않은 칸"):
            entries_of(raw(difficulty="hard"))

    def test_doc_key_는_growth_로_시작한다(self):
        with pytest.raises(ValueError, match="growth."):
            entries_of(raw(doc_key="activity.routine.x"))

    def test_습관_교정은_36개월_미만이면_실패한다(self):
        """DB 의 CHECK (row_type <> 'habit_strategy' OR min_month >= 36) 와 같다."""
        habit = raw(
            row_type="habit_strategy",
            routine_category="habit",
            trigger_tags=["bored"],
            min_month=HABIT_MIN_MONTH - 1,
            max_month=60,
        )
        with pytest.raises(ValueError, match=str(HABIT_MIN_MONTH)):
            entries_of(habit)
        entries_of({**habit, "min_month": HABIT_MIN_MONTH})

    def test_앞_단계는_같은_파일_안의_루틴_단계여야_한다(self):
        with pytest.raises(ValueError, match="next_step_of"):
            entries_of(raw(next_step_of=K1))  # 자기 자신
        with pytest.raises(ValueError, match="next_step_of"):
            entries_of(
                learning("growth.learn.art.a", 12, 36), raw(next_step_of="growth.learn.art.a")
            )
        with pytest.raises(ValueError, match="next_step_of"):
            entries_of(raw(), learning("growth.learn.art.a", 12, 36, next_step_of=K1))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"doc_key": "growth.routine.self_care.toothbrush"},  # .step<N> 이 없다
            {"doc_key": "growth.routine.self_care.toothbrush.alt"},
            {"doc_key": "growth.routine.toothbrush.step1"},  # 카테고리 칸이 없다
            {"routine_category": None},
            {"routine_category": "mealtime"},  # doc_key 의 카테고리와 다르다
        ],
    )
    def test_자립_단계의_doc_key_는_카테고리와_사슬_이름을_담는다(self, overrides):
        with pytest.raises(ValueError, match="doc_key"):
            entries_of(raw(**overrides))

    def test_사슬_이름은_step_앞까지다(self):
        assert chain_key(K2) == "growth.routine.self_care.toothbrush"
        with pytest.raises(ValueError):
            chain_key("growth.learn.art.a")

    def test_doc_key_가_겹치면_실패한다(self):
        with pytest.raises(ValueError, match="겹친다"):
            entries_of(raw(), raw())

    def test_area_는_고시_영역이다(self):
        assert AREAS == {
            "basic_life",
            "physical_health",
            "communication",
            "social",
            "art",
            "nature",
        }


class TestToRow:
    def test_id_는_doc_key_로_늘_같다(self):
        (entry,) = entries_of(raw())
        assert entry.to_row().id == entry.to_row().id == doc_id(K1)

    def test_앞_단계는_그_행의_id_로_바뀐다(self):
        first, second, _ = entries_of(*chain())
        assert first.to_row().next_step_of is None
        assert second.to_row().next_step_of == first.to_row().id

    def test_포트가_읽는_칸이_채워진다(self):
        (entry,) = entries_of(raw(trigger_tags=["tired"]))
        row = entry.to_row()
        assert (row.row_type, row.routine_category, row.setting) == (
            "routine_step",
            "self_care",
            "indoor",
        )
        assert row.materials == ("칫솔",) and row.trigger_tags == ("tired",)
        assert row.tags == ("self_care", "assistance:full_assist")
        assert (row.min_month, row.max_month) == (12, 36)


class TestFromSeed:
    async def test_검수를_마친_행만_싣는다(self, monkeypatch):
        entries = entries_of(
            learning("growth.learn.art.approved", 12, 36),
            learning(
                "growth.learn.art.draft", 12, 36, status="draft", reviewed_by=None, reviewed_at=None
            ),
        )
        monkeypatch.setattr("app.agents.growth.store.inmemory.growth_doc_seed", lambda: entries)
        docs = InMemoryGrowthDocs.from_seed()
        rows = await docs.search(
            months=20,
            row_type="learning_activity",
            routine_category=None,
            trigger_tags=(),
            query="",
            limit=99,
        )
        assert [r.doc_key for r in rows] == ["growth.learn.art.approved"]
