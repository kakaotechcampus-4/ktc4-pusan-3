"""식단 후보 출력 검증 — 순서가 결과를 바꾼다.

  1. 후보 확인      샘플 밖 `menu_key` · 같은 `menu_key` 를 두 번 낸 것은 거절한다
  2. 사후 안전 필터  카탈로그 행으로 `filter_food_safety` 를 다시 건다. 걸린 후보는 거절이 아니라
                     풀에서 뺀다(`removed`) — 모델에게 사유를 돌려주지 않는다 (§5-2)
  3. reason 문장     수치 · 진단 · 처방 · 몸 상태 · 또래 비교를 막는다(`nutrient_wording`)
  4. 근거 대조       이번 run 에서 조회하지 않은 근거 id 를 인용하면 후보를 거절한다
  5. build()         6개월이 안 지난 아이 기록 근거 수로 `kind` 를 정한다.
                     이름 · 재료는 카탈로그 행에서 온다. 일반 추천이면 고른 메뉴(레시피 카탈로그
                     행) 자체가 근거로 붙는다 — 문서 행이 없는 월령에도 근거 0행이 되지 않게(#442)

안전에 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다. 걸린 후보는 고치지 않고 통째로 뺀다.
사유는 코드로만 남긴다 — 메뉴 이름 · 근거 문장을 싣지 않는다(로그로 흘러간다).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID

from app.agents.common.evidence import RankedEvidence, cite, unseen
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionRejected, build
from app.agents.food.context import MealPick
from app.agents.food.nutrient_wording import wording_problem
from app.agents.food.readouts import GENERAL_REASON_TODDLER, GENERAL_REASON_WEANING, READOUTS
from app.agents.food.result import ErrorCode
from app.agents.food.schemas.common import EvidenceRef, MealSlot
from app.agents.food.schemas.recommend import MealCandidate
from app.agents.food.store.ports import RECIPE_SOURCES, FoodDocRow, MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety
from app.rules.age import Stage
from app.rules.menu_evidence import menu_evidence_id
from app.rules.menu_key import normalize_menu_key

Seen = Mapping[UUID, RankedEvidence]
Docs = Mapping[UUID, FoodDocRow]

# 아이 기록 근거가 0행이면 build() 가 reason 을 이 문구로 덮어쓴다(kind="general"). 화면이 일반
# 추천임을 표시하고 쌓인 기록 건수를 함께 보여 준다(루트 §2). 이유기 아이는 "이 시기 아기들" 문구다
GENERAL_REASON = READOUTS.get(GENERAL_REASON_TODDLER).template
GENERAL_REASON_INFANT = READOUTS.get(GENERAL_REASON_WEANING).template

# 일반 추천에 붙는 메뉴 근거의 한 줄. 코드 문구다(모델이 쓰지 않는다). 05 화면에 그대로 나가서
# 사실만 쓴다 — 후보 풀의 레시피는 재료 정리 표를 사람이 검수한 것만 들어온다(resolved)
MENU_EVIDENCE_NOTE = "농식품정보원 공개 레시피 중 재료가 검수된 메뉴예요."


class RejectReason(StrEnum):
    UNKNOWN_MENU = "unknown_menu"
    DUPLICATE_MENU = "duplicate_menu"
    WORDING = "wording"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    BUILD = "build_rejected"


# 모델에게 돌려줄 설명 — 무엇을 고치면 되는지만. 모델이 쓴 값과 걸린 낱말은 싣지 않는다
GUIDANCE: dict[RejectReason, str] = {
    RejectReason.UNKNOWN_MENU: "menu_key 는 보여 준 후보 목록에 있는 것만 쓴다.",
    RejectReason.DUPLICATE_MENU: "같은 menu_key 를 두 번 내지 않는다. 다른 후보로 바꾼다.",
    RejectReason.WORDING: (
        "reason 에 수치 · 진단 · 처방 · 몸 상태 · 또래 비교를 쓰지 않는다. "
        "이 아이의 기록에서 고른 이유와 지금인 이유만 쓴다."
    ),
    RejectReason.UNKNOWN_EVIDENCE: (
        "evidence 에는 search_food_memory · guide_weaning_stage 결과나 [예시] 에 있던 id 만 쓴다."
    ),
    RejectReason.BUILD: (
        "아이 기록을 인용하지 않으면 [예시] 문서 행 id 를 하나 이상 인용한다. "
        "싫어하는 것을 근거로 썼으면 무엇을 피했는지 reason 에 쓴다."
    ),
}


@dataclass(frozen=True)
class Rejection:
    index: int  # 후보 순서 (0부터)
    reason: RejectReason


@dataclass(frozen=True)
class Review:
    picks: tuple[MealPick, ...]
    rejections: tuple[Rejection, ...]
    removed: tuple[str, ...] = ()  # 안전 필터로 빠진 menu_key. 로그 · 재샘플링용이다


def review_candidates(
    candidates: Sequence[MealCandidate],
    *,
    sampled: Mapping[str, MenuCatalogRow],
    seen: Seen,
    docs: Docs,
    entries: Sequence[SafetyEntry],
    stage: Stage,
    months: int,
    today: date,
) -> Review:
    """후보를 순서대로 검사해 통과한 것은 `MealPick` 으로, 걸린 것은 사유로 돌려준다.

    `entries` 는 `build_gate` 가 run state 에 담은 health_safety 행이다 — 여기서 다시 읽지 않는다.
    `today` 는 `build()` 의 6개월 판정 기준일이다.
    """
    rejections: list[Rejection] = []
    chosen: list[tuple[int, MealCandidate, MenuCatalogRow]] = []
    taken: set[str] = set()
    for index, candidate in enumerate(candidates):
        # 띄어쓰기 · 대소문자만 다르게 옮겨 쓴 것은 같은 키로 본다 — 보여 준 후보 안에서만 찾는다
        key = normalize_menu_key(candidate.menu_key)
        row = sampled.get(key)
        if row is None:
            rejections.append(Rejection(index, RejectReason.UNKNOWN_MENU))
        elif key in taken:
            rejections.append(Rejection(index, RejectReason.DUPLICATE_MENU))
        else:
            taken.add(key)
            chosen.append((index, candidate, row))
    if rejections:
        return Review(picks=(), rejections=tuple(rejections))

    # 2. 사후 안전 필터 — 모델이 고른 행 그대로 다시 건다. 막힌 것 · 확인 못 한 것은 모두 뺀다
    verdict = filter_food_safety([row for _, _, row in chosen], entries, stage, months=months)
    passed = {row.menu_key for row in verdict.passed}
    removed = tuple(row.menu_key for _, _, row in chosen if row.menu_key not in passed)

    picks: list[MealPick] = []
    for index, candidate, row in chosen:
        if row.menu_key not in passed:
            continue
        reason = _check(candidate, seen=seen, docs=docs)
        if isinstance(reason, RejectReason):
            rejections.append(Rejection(index, reason))
            continue
        try:
            draft = build(
                agent="food",
                content=row.display_name,
                reason=candidate.reason.strip(),
                citations=reason,
                general_reason=GENERAL_REASON_INFANT
                if stage == "infant_weaning"
                else GENERAL_REASON,
                general_basis=_menu_basis(row),
                allergens=verdict.allergens.get(row.menu_key, ()),
                items=row.ingredients,
                today=today,
            )
        except SuggestionRejected:
            rejections.append(Rejection(index, RejectReason.BUILD))
            continue
        picks.append(
            MealPick(draft=draft, menu_key=row.menu_key, meal_slot=MealSlot(candidate.meal_slot))
        )
    return Review(picks=tuple(picks), rejections=tuple(rejections), removed=removed)


def error_code(rejections: Sequence[Rejection]) -> str:
    """거절 묶음의 대표 오류 코드. 로그 집계용이다.

    조회하지 않은 근거 id 가 하나라도 있으면 공통 `EVIDENCE_REQUIRED` 다 — 모델이 id 를 지어낸
    것이라 다른 거절과 따로 세야 한다. 나머지는 `INVALID_ARGS` 다.
    """
    if any(r.reason is RejectReason.UNKNOWN_EVIDENCE for r in rejections):
        return ErrorCode.EVIDENCE_REQUIRED
    return ErrorCode.INVALID_ARGS


def explain(rejections: Sequence[Rejection]) -> str:
    """모델이 읽는 거절 설명. 후보 번호(1부터)와 고칠 방향만 싣는다."""
    return " / ".join(f"{r.index + 1}번 후보: {GUIDANCE[r.reason]}" for r in rejections)


def _check(
    candidate: MealCandidate, *, seen: Seen, docs: Docs
) -> tuple[EvidenceCitation, ...] | RejectReason:
    """reason 문장 → 근거 대조 → 인용 만들기. 걸리면 사유를, 아니면 인용을 돌려준다."""
    # "먹은 식사가 주어" 요구는 영양소 분석 요약에만 맞는 규칙이다 — 추천 이유에는 걸지 않는다
    problem = wording_problem(candidate.reason)
    if problem is not None and problem != "no_meal_subject":
        return RejectReason.WORDING

    known: set[UUID] = {*seen, *docs}
    if unseen((pick.id for pick in candidate.evidence), known):
        return RejectReason.UNKNOWN_EVIDENCE

    # 인용의 종류는 모델이 쓴 값이 아니라 조회 결과에서 채운다. 6개월 판정은 build() 가 한다
    found = [(pick, _lookup(seen, docs, pick)) for pick in candidate.evidence]
    return tuple(_cite(item, note=pick.note) for pick, item in found)


def _lookup(seen: Seen, docs: Docs, pick: EvidenceRef) -> RankedEvidence | FoodDocRow:
    key = UUID(pick.id)  # unseen 이 UUID 가 아닌 값을 이미 걸렀다
    found = seen.get(key) or docs.get(key)
    assert found is not None
    return found


def _menu_basis(row: MenuCatalogRow) -> tuple[EvidenceCitation, ...]:
    """고른 메뉴 자체를 일반 추천의 근거로 (#442). 개인화 추천이면 `build()` 가 버린다.

    레시피 카탈로그 행만 가리킬 수 있다(id 는 `rules/menu_evidence`). 레시피 번호가 없는 행은
    비운다 — 그때는 지금처럼 문서 행을 인용해야 일반 추천이다. 운영 후보 풀은 늘 레시피 행이다.
    """
    if row.source not in RECIPE_SOURCES or not row.source_id:
        return ()
    ref = Ref(kind="recipe_catalog", id=menu_evidence_id(row.source, row.source_id))
    return (EvidenceCitation(ref=ref, note=MENU_EVIDENCE_NOTE, label=row.display_name),)


def _cite(item: RankedEvidence | FoodDocRow, *, note: str) -> EvidenceCitation:
    """기억은 `cite()` 로, 문서 행은 `food_doc` 근거로. 문서 행은 기피가 없다(polarity 0)."""
    if isinstance(item, RankedEvidence):
        return cite(item, note=note)
    return EvidenceCitation(ref=Ref(kind="food_doc", id=item.id), note=note, label=item.title)
