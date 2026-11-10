"""식단 후보 출력 검증 — 순서가 결과를 바꾼다.

  1. 후보 확인      샘플 밖 `menu_key` · 같은 `menu_key` 를 두 번 낸 것은 거절한다
  2. 사후 안전 필터  카탈로그 행으로 `filter_food_safety` 를 다시 건다. 걸린 후보는 거절이 아니라
                     풀에서 뺀다(`removed`) — 모델에게 사유를 돌려주지 않는다 (§5-2)
  3. reason 문장     수치 · 진단 · 처방 · 몸 상태 · 또래 비교를 막는다(`nutrient_wording`)
  4. 음식 언급       reason 이 등록 알레르기와 겹치거나 연령 규칙이 막는 음식을 말하면 거절한다
                     (`check_foods`). 후보는 안전한데 이유에 "견과류가 들어가" 라고 쓰는 것을 막는다
  5. 근거 대조       이번 run 에서 조회하지 않은 근거 id 를 인용하면 후보를 거절한다
  6. build()         6개월이 안 지난 아이 기록 근거 수로 `kind` 를 정한다.
                     이름 · 재료는 카탈로그 행에서 온다. 일반 추천이면 고른 메뉴(레시피 카탈로그
                     행) 자체가 근거로 붙는다 — 문서 행이 없는 월령에도 근거 0행이 되지 않게(#442)

  7. 생성 후보      보호자가 재료 · 요리 종류를 정해 물었고 레시피북이 모자랄 때만 받는다(#740).
                     모델이 이름 · 재료를 쓰므로 형식 · 재료 조건 · 최근 이름 · 풀 이름을 보고 같은
                     안전 필터를 이름 + 재료로 건다. 근거가 없으면 요청 기반 일반 추천이다

안전에 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다. 걸린 후보는 고치지 않고 통째로 뺀다.
사유는 코드로만 남긴다 — 메뉴 이름 · 근거 문장을 싣지 않는다(로그로 흘러간다).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from enum import StrEnum
from uuid import UUID

from app.agents.common.evidence import RankedEvidence, cite, unseen
from app.agents.common.food_safety import filter_food_safety
from app.agents.common.food_words import mentions_blocked_food
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionRejected, build
from app.agents.food.context import MealPick
from app.agents.food.meal_request import condition_stems, names_mention
from app.agents.food.nutrient_wording import wording_problem
from app.agents.food.preparation_items import preparation_items
from app.agents.food.readouts import (
    GENERAL_REASON_REQUESTED,
    GENERAL_REASON_TODDLER,
    GENERAL_REASON_WEANING,
    READOUTS,
)
from app.agents.food.result import ErrorCode
from app.agents.food.schemas.common import EvidenceRef, MealSlot
from app.agents.food.schemas.recommend import GeneratedMeal, MealCandidate
from app.agents.food.store.ports import RECIPE_SOURCES, FoodDocRow, MenuCatalogRow, SafetyEntry
from app.core.recipe_source import RECIPE_SOURCE_INFO
from app.rules.age import Stage
from app.rules.menu_evidence import menu_evidence_id
from app.rules.menu_key import normalize_menu_key

Seen = Mapping[UUID, RankedEvidence]
Docs = Mapping[UUID, FoodDocRow]

# 아이 기록 근거가 0행이면 build() 가 reason 을 이 문구로 덮어쓴다(kind="general"). 화면이 일반
# 추천임을 표시하고 쌓인 기록 건수를 함께 보여 준다(루트 §2). 이유기 아이는 "이 시기 아기들" 문구다
GENERAL_REASON = READOUTS.get(GENERAL_REASON_TODDLER).template
GENERAL_REASON_INFANT = READOUTS.get(GENERAL_REASON_WEANING).template
# 보호자가 재료 · 요리 종류를 정해 물었고 아이 기록 근거가 없으면 이 문구다 — 레시피 · 생성 모두
GENERAL_REASON_REQUEST = READOUTS.get(GENERAL_REASON_REQUESTED).template


class RejectReason(StrEnum):
    UNKNOWN_MENU = "unknown_menu"
    DUPLICATE_MENU = "duplicate_menu"
    WORDING = "wording"
    FOOD_MENTION = "food_mention"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    BUILD = "build_rejected"
    REQUEST_MISMATCH = "request_mismatch"
    GENERATED_NOT_ALLOWED = "generated_not_allowed"
    GENERATED_TOO_MANY = "generated_too_many"
    GENERATED_FORMAT = "generated_format"
    GENERATED_RECENT = "generated_recent"
    REQUEST_BLOCKED = "request_blocked"


# 모델에게 돌려줄 설명 — 무엇을 고치면 되는지만. 모델이 쓴 값과 걸린 낱말은 싣지 않는다
GUIDANCE: dict[RejectReason, str] = {
    RejectReason.UNKNOWN_MENU: "menu_key 는 보여 준 후보 목록에 있는 것만 쓴다.",
    RejectReason.DUPLICATE_MENU: "같은 menu_key 를 두 번 내지 않는다. 다른 후보로 바꾼다.",
    RejectReason.WORDING: (
        "reason 에 수치 · 진단 · 처방 · 몸 상태 · 또래 비교를 쓰지 않는다. "
        "이 아이의 기록에서 고른 이유와 지금인 이유만 쓴다."
    ),
    # 막힌 음식 이름도 알레르기 이름도 싣지 않는다 — 사유가 로그와 모델 입력으로 흘러간다
    RejectReason.FOOD_MENTION: (
        "reason 에는 고른 메뉴와 아이 기록에서 고른 이유만 쓴다. "
        "보호자가 요청에 쓴 알레르기 · 피할 음식 이름과 '알레르기를 고려해' 같은 말, "
        "후보 목록에 없는 음식 이름은 쓰지 않는다."
    ),
    RejectReason.UNKNOWN_EVIDENCE: (
        "evidence 에는 search_food_memory · guide_weaning_stage 결과나 [예시] 에 있던 id 만 쓴다."
    ),
    RejectReason.BUILD: (
        "아이 기록을 인용하지 않으면 [예시] 문서 행 id 를 하나 이상 인용한다. "
        "싫어하는 것을 근거로 썼으면 무엇을 피했는지 reason 에 쓴다."
    ),
    RejectReason.REQUEST_MISMATCH: (
        "보호자가 정한 재료가 이름이나 재료에 든 메뉴만 낸다. 모자라면 generated 로 채운다."
    ),
    RejectReason.GENERATED_NOT_ALLOWED: (
        "generated 는 보호자가 재료나 요리 종류를 정해 물었고 requested_food 에 보호자 말 그대로 "
        "적었을 때만, 돌 이후 아이에게 쓴다. 아니면 후보 목록에서 menu_key 로 고른다."
    ),
    RejectReason.GENERATED_TOO_MANY: (
        "generated 는 후보 목록의 '요청과 맞음' 메뉴로 채우지 못하는 자리만큼 낸다. "
        "후보 목록의 '요청과 맞음' 메뉴를 먼저 고른다."
    ),
    RejectReason.GENERATED_FORMAT: (
        "메뉴 이름과 재료에 숫자 · 단위 · 괄호를 쓰지 않는다. 재료는 이름만 적고 8개 이하로 쓴다."
    ),
    RejectReason.GENERATED_RECENT: "최근에 추천한 메뉴는 다시 내지 않는다. 다른 메뉴로 바꾼다.",
    # 왜 못 쓰는지(알레르기 · 연령)는 싣지 않는다 — 사유를 알면 낱말만 바꿔 같은 음식을 다시 낸다
    RejectReason.REQUEST_BLOCKED: (
        "이번 요청의 재료 · 요리 종류로는 generated 를 쓰지 않는다. "
        "후보 목록에서 menu_key 로 고른다."
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


@dataclass(frozen=True)
class RequestContext:
    """보호자가 이번 요청에서 재료 · 요리 종류를 정해 물었는가, 그리고 검증에 필요한 풀 정보.

    `food` 는 모델이 적은 `requested_food` 중 **보호자 말에 있다고 인정된 것**이다 — 비어 있으면
    조건이 인정되지 않은 것이라 생성 후보를 받지 않고 이유 문구도 지금 그대로다.
    """

    food: str | None = None
    kind: str | None = None  # ingredient · cuisine
    # 샘플 안의 조건 맞는 레시피 수(요리 종류 조건이면 0). 생성 후보 자리는 3 - 이 수다
    matched: int = 0
    pool: Mapping[str, MenuCatalogRow] = field(default_factory=dict)  # 이번 run 후보 풀
    recipe_keys: frozenset[str] = frozenset()  # 레시피북 전체(풀 밖 = 막힘 · 최근 추천)
    recent_keys: frozenset[str] = frozenset()  # 최근 추천한 메뉴
    # 보호자가 정한 조건이 아이에게 막힌 음식이다(알레르기 · 연령). 조건을 걸지 않고 생성도 받지
    # 않는다 — 생성 메뉴는 전부 필터에 걸리고, 걸린 이름이 재호출로 모델에게 다시 간다(T49)
    blocked: bool = False

    @property
    def accepted(self) -> bool:
        return bool(self.food)


NO_REQUEST = RequestContext()
BLOCKED_REQUEST = RequestContext(blocked=True)

# 생성 후보의 이름 · 재료에 쓰면 안 되는 것 — 정확한 레시피 · 양 금지(루트 §1)
_FORMAT_BAD = re.compile(
    r"\d|[()\[\]{}（）]|그램|킬로|밀리|리터|큰술|작은술|티스푼|스푼"
    r"|(?:^|\s)[반한두세네]\s?(?:개|조각|줌|모|장|알|컵|병)(?:\s|$)"
)
# 생성 메뉴를 받는 단계 — 이유기는 질감 · 단계를 레시피 `stage_min` 으로만 봐서 생성에 걸 규칙이
# 없다
_GENERATION_STAGES: frozenset[str] = frozenset({"toddler", "preschool"})
# 생성 후보의 안전 판정 행 — 출처 없는 임시 행이고 저장하지 않는다
_MANUAL_SOURCE = "manual"


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
    generated: Sequence[GeneratedMeal] = (),
    request: RequestContext = NO_REQUEST,
) -> Review:
    """후보를 순서대로 검사해 통과한 것은 `MealPick` 으로, 걸린 것은 사유로 돌려준다.

    `entries` 는 `build_gate` 가 run state 에 담은 health_safety 행이다 — 여기서 다시 읽지 않는다.
    `today` 는 `build()` 의 6개월 판정 기준일이다.
    """

    def mentions_blocked(reason: str, allow: Sequence[str]) -> bool:
        return mentions_blocked_food(reason, entries, stage=stage, months=months, allow=allow)

    # 생성할 수 없는 단계(돌 이전)는 조건 검사 · 요청 기반 문구를 걸지 않는다 — 걸면 레시피북에
    # 그 재료가 없을 때 모델이 빠져나갈 길이 없다(레시피는 조건 불일치, 생성은 단계로 막힌다).
    # 지금처럼 레시피북 메뉴만 낸다
    if stage not in _GENERATION_STAGES:
        request = NO_REQUEST
    stems = condition_stems(request.food) if request.food and request.kind == "ingredient" else []
    rejections: list[Rejection] = []
    # (후보 번호, 모델이 낸 후보, 카탈로그 행, 생성 메뉴인가)
    chosen: list[tuple[int, MealCandidate | GeneratedMeal, MenuCatalogRow, bool]] = []
    taken: set[str] = set()
    for index, candidate in enumerate(candidates):
        # 띄어쓰기 · 대소문자만 다르게 옮겨 쓴 것은 같은 키로 본다 — 보여 준 후보 안에서만 찾는다
        key = normalize_menu_key(candidate.menu_key)
        row = sampled.get(key)
        if row is None:
            rejections.append(Rejection(index, RejectReason.UNKNOWN_MENU))
        elif key in taken:
            rejections.append(Rejection(index, RejectReason.DUPLICATE_MENU))
        elif stems and not names_mention((row.display_name, *row.ingredients), stems):
            rejections.append(Rejection(index, RejectReason.REQUEST_MISMATCH))
        else:
            taken.add(key)
            chosen.append((index, candidate, row, False))

    gone: list[str] = []  # 레시피북에 있는데 풀에 없는 이름으로 돌아온 생성 후보 — 막힌 것으로 본다
    for offset, meal in enumerate(generated):
        index = len(candidates) + offset
        outcome = _review_generated(
            meal,
            position=offset,
            request=request,
            stems=stems,
            stage=stage,
            taken=taken,
            today=today,
        )
        if isinstance(outcome, RejectReason):
            rejections.append(Rejection(index, outcome))
        elif outcome is None:
            gone.append(normalize_menu_key(meal.name))
        else:
            taken.add(outcome.menu_key)
            chosen.append((index, meal, outcome, outcome.source == _MANUAL_SOURCE))
    if rejections:
        return Review(picks=(), rejections=tuple(rejections))

    # 2. 사후 안전 필터 — 레시피는 고른 행 그대로, 생성은 이름 + 재료 임시 행으로 다시 건다.
    #    막힌 것 · 확인 못 한 것은 모두 뺀다
    verdict = filter_food_safety([row for _, _, row, _ in chosen], entries, stage, months=months)
    passed = {row.menu_key for row in verdict.passed}
    removed = (*(row.menu_key for _, _, row, _ in chosen if row.menu_key not in passed), *gone)

    picks: list[MealPick] = []
    for index, candidate, row, is_generated in chosen:
        if row.menu_key not in passed:
            continue
        reason = _check(candidate, seen=seen, docs=docs, mentions_blocked=mentions_blocked)
        if isinstance(reason, RejectReason):
            rejections.append(Rejection(index, reason))
            continue
        try:
            draft = build(
                agent="food",
                content=row.display_name,
                reason=candidate.reason.strip(),
                citations=reason,
                general_reason=_general_reason(stage, request),
                # 생성 메뉴는 메뉴 근거가 없다 — 그 요청이 기준이다(`requested`)
                general_basis=() if is_generated else _menu_basis(row),
                allergens=verdict.allergens.get(row.menu_key, ()),
                # 준비물만 주재료로 줄인다. 안전 필터는 위에서 전체 재료 · 코드로 이미 걸렀다 (#474)
                items=preparation_items(row.main_ingredients),
                requested=request.accepted,
                today=today,
            )
        except SuggestionRejected:
            rejections.append(Rejection(index, RejectReason.BUILD))
            continue
        picks.append(
            MealPick(draft=draft, menu_key=row.menu_key, meal_slot=MealSlot(candidate.meal_slot))
        )
    return Review(picks=tuple(picks), rejections=tuple(rejections), removed=tuple(removed))


def _general_reason(stage: Stage, request: RequestContext) -> str:
    """아이 기록 근거가 없을 때의 이유 문구. 보호자가 조건을 정해 물었으면 그 요청이 기준이다."""
    if request.accepted:
        return GENERAL_REASON_REQUEST
    return GENERAL_REASON_INFANT if stage == "infant_weaning" else GENERAL_REASON


def _review_generated(
    meal: GeneratedMeal,
    *,
    position: int,
    request: RequestContext,
    stems: Sequence[str],
    stage: Stage,
    taken: set[str],
    today: date,
) -> MenuCatalogRow | RejectReason | None:
    """생성 후보 하나의 앞 검사. 통과하면 안전 필터에 걸 행을, 거절이면 사유를 돌려준다.

    레시피북에 있는 이름이면 그 레시피 행(검수된 재료 · 출처)을 쓰고, 풀 밖 레시피(막힘 · 최근
    추천)와 같은 이름이면 `None` — 막힌 메뉴가 이름만 바꿔 돌아오지 않게 뺀다.
    """
    if request.blocked:
        return RejectReason.REQUEST_BLOCKED
    if not request.accepted or stage not in _GENERATION_STAGES:
        return RejectReason.GENERATED_NOT_ALLOWED
    allowed = 3 - request.matched
    if position >= allowed:
        return RejectReason.GENERATED_TOO_MANY
    parts = (meal.name, *meal.main_ingredients, *meal.ingredients)
    if any(_FORMAT_BAD.search(part) for part in parts):
        return RejectReason.GENERATED_FORMAT
    if stems and not names_mention(parts, stems):
        return RejectReason.REQUEST_MISMATCH
    key = normalize_menu_key(meal.name)
    if key in request.pool:
        return RejectReason.DUPLICATE_MENU if key in taken else request.pool[key]
    if key in request.recipe_keys:
        return None
    if key in request.recent_keys:
        return RejectReason.GENERATED_RECENT
    if key in taken:
        return RejectReason.DUPLICATE_MENU
    return MenuCatalogRow(
        menu_key=key,
        display_name=meal.name,
        source=_MANUAL_SOURCE,
        resolved=True,
        synced_at=datetime.combine(today, time.min, tzinfo=UTC),
        # 주재료도 안전 필터에 건다 — 모델이 주재료만 쓰고 재료 목록에서 빠뜨려도 걸리게
        ingredients=tuple(dict.fromkeys((*meal.ingredients, *meal.main_ingredients))),
        main_ingredients=tuple(meal.main_ingredients),
    )


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
    candidate: MealCandidate | GeneratedMeal,
    *,
    seen: Seen,
    docs: Docs,
    mentions_blocked: Callable[[str, Sequence[str]], bool],
) -> tuple[EvidenceCitation, ...] | RejectReason:
    """reason 문장 → 음식 언급 → 근거 대조 → 인용 만들기. 걸리면 사유를, 아니면 인용을 돌려준다."""
    # "먹은 식사가 주어" 요구는 영양소 분석 요약에만 맞는 규칙이다 — 추천 이유에는 걸지 않는다
    problem = wording_problem(candidate.reason)
    if problem is not None and problem != "no_meal_subject":
        return RejectReason.WORDING

    if mentions_blocked(candidate.reason, _avoided(candidate, seen)):
        return RejectReason.FOOD_MENTION

    known: set[UUID] = {*seen, *docs}
    if unseen((pick.id for pick in candidate.evidence), known):
        return RejectReason.UNKNOWN_EVIDENCE

    # 인용의 종류는 모델이 쓴 값이 아니라 조회 결과에서 채운다. 6개월 판정은 build() 가 한다
    found = [(pick, _lookup(seen, docs, pick)) for pick in candidate.evidence]
    return tuple(_cite(item, note=pick.note) for pick, item in found)


def _avoided(candidate: MealCandidate | GeneratedMeal, seen: Seen) -> tuple[str, ...]:
    """후보가 인용한 기피 근거의 대상. 기피를 인용하면 `build()` 가 이유에 피한 것을 쓰라고
    요구하므로, 그 음식은 막힌 음식이어도 이유에 써도 된다. 조회하지 않은 id 는 다음 검사가
    거른다."""
    avoided: list[str] = []
    for pick in candidate.evidence:
        try:
            item = seen.get(UUID(pick.id))
        except ValueError:
            continue
        if item is not None and item.is_avoidance:
            avoided.append(item.label)
    return tuple(avoided)


def _lookup(seen: Seen, docs: Docs, pick: EvidenceRef) -> RankedEvidence | FoodDocRow:
    key = UUID(pick.id)  # unseen 이 UUID 가 아닌 값을 이미 걸렀다
    found = seen.get(key) or docs.get(key)
    assert found is not None
    return found


def _menu_basis(row: MenuCatalogRow) -> tuple[EvidenceCitation, ...]:
    """고른 메뉴 자체를 일반 추천의 근거로 (#442). 개인화 추천이면 `build()` 가 버린다.

    레시피 카탈로그 행만 가리킬 수 있다(id 는 `rules/menu_evidence`). 레시피 번호가 없는 행은
    비운다 — 그때는 지금처럼 문서 행을 인용해야 일반 추천이다. 운영 후보 풀은 늘 레시피 행이다.

    같은 이름을 합친 행은 합쳐진 레시피를 모두 단다(`merged_from`). 그 메뉴의 재료 · 알레르기가
    모두에서 왔고, 대표만 달면 대표가 소스에서 내려간 뒤 다른 소스로 남은 메뉴를 05 가 숨긴다.
    한 줄 문구는 소스 정보 표의 `note` 다(코드 문구, #510).
    """
    if row.source not in RECIPE_SOURCES or not row.source_id:
        return ()
    pairs = ((row.source, row.source_id), *row.merged_from)
    return tuple(
        EvidenceCitation(
            ref=Ref(kind="recipe_catalog", id=menu_evidence_id(source, source_id)),
            note=RECIPE_SOURCE_INFO[source].note,  # type: ignore[index] — RECIPE_SOURCES 와 같은 키다
            label=row.display_name,
        )
        for source, source_id in pairs
        if source in RECIPE_SOURCES
    )


def _cite(item: RankedEvidence | FoodDocRow, *, note: str) -> EvidenceCitation:
    """기억은 `cite()` 로, 문서 행은 `food_doc` 근거로. 문서 행은 기피가 없다(polarity 0)."""
    if isinstance(item, RankedEvidence):
        return cite(item, note=note)
    return EvidenceCitation(ref=Ref(kind="food_doc", id=item.id), note=note, label=item.title)
