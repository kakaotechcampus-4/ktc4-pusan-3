"""`propose_meal_candidates` — 모델이 고른 후보를 코드가 검증해 추천 초안으로 바꾼다.

순서: 샘플 밖 menu_key → 사후 안전 필터 → reason 문장 규칙 → 조회하지 않은 근거 → 개인화 · 일반 판정
→ 개수. 메뉴 이름과 재료는 모델이 아니라 카탈로그 행에서 온다. 레시피 · 아이 · 기록은 전부 가짜다.
"""

from datetime import date
from uuid import UUID, uuid4

import pytest

from app.agents.common.evidence import RankedEvidence
from app.agents.common.refs import Ref
from app.agents.food.context import FoodContext
from app.agents.food.readouts import GENERAL_REASON_TODDLER, GENERAL_REASON_WEANING, READOUTS
from app.agents.food.result import ErrorCode
from app.agents.food.schemas.common import EvidenceRef
from app.agents.food.schemas.recommend import MealCandidate, ProposeMealCandidatesArgs
from app.agents.food.store import SafetyEntry
from app.agents.food.tools.recommend import propose_meal_candidates
from app.agents.food.tools.safety import filter_food_safety
from app.rules.menu_evidence import menu_evidence_id
from tests.unit.agents.food.support import MEAL_DOC, context, day, doc_row, menu_row

MILK = [SafetyEntry(kind="allergy", label="우유", status="active")]
GENERAL_REASON = READOUTS.get(GENERAL_REASON_TODDLER).template

TOFU = menu_row("두부조림", source="mafra_recipe", ingredients=("두부", "간장"))
EGGROLL = menu_row(
    "계란말이",
    source="mafra_recipe",
    ingredients=("계란", "당근"),
    allergen_codes=frozenset({1}),
)
MILKBREAD = menu_row(
    "우유식빵", source="mafra_recipe", ingredients=("우유", "식빵"), allergen_codes=frozenset({2})
)
SPINACH = menu_row("시금치나물", source="mafra_recipe", ingredients=("시금치", "참기름"))


# 일반 추천은 문서 행이 근거다 (루트 §2). 기본 후보는 이 행을 인용하고, shown() 이 보여 준다
DOC_CITE = EvidenceRef(kind="food_doc", id=str(MEAL_DOC.id), note="식단 자료를 참고함")


def pick(
    menu_key: str, *, reason: str = "어제 급식과 겹치지 않아서 골랐어요", **kwargs
) -> MealCandidate:
    kwargs.setdefault("evidence", [DOC_CITE])
    return MealCandidate(
        menu_key=menu_key, meal_slot=kwargs.pop("meal_slot", "dinner"), reason=reason, **kwargs
    )


def args(*candidates: MealCandidate) -> ProposeMealCandidatesArgs:
    return ProposeMealCandidatesArgs(candidates=list(candidates))


def shown(*rows, safety=(), exhausted: bool = False, birth: date | None = None) -> FoodContext:
    """모델에게 `rows` 를 후보로 보여 준 뒤의 context."""
    ctx = context(safety=safety) if birth is None else context(safety=safety, birth=birth)
    ctx.state.sampled = {row.menu_key: row for row in rows}
    ctx.state.exhausted = exhausted
    ctx.state.seen_docs[MEAL_DOC.id] = MEAL_DOC
    return ctx


def seen_affinity(
    ctx: FoodContext, label: str, *, polarity: int = 1, days_ago: int = 1, tier: int = 1
) -> UUID:
    """search_food_memory 가 이 근거를 돌려줬다고 run state 에 적는다."""
    key = uuid4()
    ctx.state.seen_evidence[key] = RankedEvidence(
        ref=Ref(kind="profile_affinity", id=key),
        tier=tier,  # type: ignore[arg-type]
        label=label,
        polarity=polarity,
        observed_on=day(days_ago),
        subject=label,
    )
    return key


def cite(
    key: UUID, *, kind: str = "profile_affinity", note: str = "딸기를 자주 먹음"
) -> EvidenceRef:
    return EvidenceRef(kind=kind, id=str(key), note=note)  # type: ignore[arg-type]


FULL = (TOFU, EGGROLL, SPINACH)


# ── 샘플 밖 menu_key ──────────────────────────────────────────────────
async def test_샘플_밖_menu_key_는_거절한다() -> None:
    ctx = shown(*FULL)

    result = await propose_meal_candidates(ctx, args(pick("두부조림"), pick("지어낸메뉴")))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "2번 후보" in result.error["message"]
    assert "지어낸메뉴" not in result.error["message"]  # 모델이 쓴 값을 message 에 싣지 않는다
    assert ctx.state.picks == ()


async def test_같은_menu_key_를_두_번_내면_거절한다() -> None:
    ctx = shown(*FULL)

    result = await propose_meal_candidates(ctx, args(pick("두부조림"), pick("두부조림")))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS


async def test_띄어쓰기만_다르게_옮겨_쓴_menu_key_는_보여_준_후보로_본다() -> None:
    ctx = shown(*FULL, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("두부 조림")))

    assert result.success
    assert [p.menu_key for p in ctx.state.picks] == ["두부조림"]


# ── 메뉴 이름 · 재료는 카탈로그에서 ───────────────────────────────────
async def test_메뉴_이름과_재료는_카탈로그_행에서_온다() -> None:
    ctx = shown(*FULL, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("두부조림"), pick("시금치나물")))

    assert result.success
    first, second = ctx.state.picks
    assert first.draft.content == "두부조림"
    assert first.draft.items == ("두부", "간장")
    assert (second.draft.content, second.draft.items) == ("시금치나물", ("시금치", "참기름"))
    assert (first.menu_key, first.meal_slot) == ("두부조림", "dinner")


# ── 사후 안전 필터 ────────────────────────────────────────────────────
async def test_우유_알레르기에_우유가_든_레시피는_사후_필터에_걸려_빠진다() -> None:
    ctx = shown(TOFU, MILKBREAD, SPINACH, safety=MILK)

    result = await propose_meal_candidates(
        ctx, args(pick("두부조림"), pick("우유식빵"), pick("시금치나물"))
    )

    assert result.success  # 거절이 아니다 — 모델에게 사유를 알리지 않는다
    assert [p.menu_key for p in ctx.state.picks] == ["두부조림", "시금치나물"]
    assert ctx.state.blocked_keys == {"우유식빵"}
    assert "우유" not in str(result.data)


async def test_코드가_빠져_있어도_재료_이름으로_걸린다() -> None:
    sneaky = menu_row("빵", source="mafra_recipe", ingredients=("밀가루", "우유"))
    ctx = shown(TOFU, sneaky, SPINACH, safety=MILK)

    await propose_meal_candidates(ctx, args(pick("두부조림"), pick("빵"), pick("시금치나물")))

    assert ctx.state.blocked_keys == {"빵"}


async def test_재료를_모르는_행은_통과가_아니라_빠진다() -> None:
    unknown = menu_row("미지의국", source="mafra_recipe", ingredients=())
    ctx = shown(TOFU, unknown, SPINACH)

    await propose_meal_candidates(ctx, args(pick("두부조림"), pick("미지의국"), pick("시금치나물")))

    assert ctx.state.blocked_keys == {"미지의국"}


async def test_빠진_후보가_있고_더_채울_길이_있으면_개수를_따지지_않고_재호출을_기다린다() -> None:
    ctx = shown(TOFU, MILKBREAD, SPINACH, safety=MILK, exhausted=False)

    result = await propose_meal_candidates(
        ctx, args(pick("두부조림"), pick("우유식빵"), pick("시금치나물"))
    )

    assert result.success
    assert len(ctx.state.picks) == 2
    assert ctx.state.notice is None  # 개수 안내는 재호출 결과를 합친 뒤에 붙는다


async def test_재호출_뒤에도_빠지면_남은_만큼만_내고_개수_안내를_붙인다() -> None:
    ctx = shown(TOFU, MILKBREAD, SPINACH, safety=MILK, exhausted=True)

    result = await propose_meal_candidates(
        ctx, args(pick("두부조림"), pick("우유식빵"), pick("시금치나물"))
    )

    assert result.success
    assert len(ctx.state.picks) == 2
    assert ctx.state.notice is not None
    assert ctx.state.notice.code == "fewer"


async def test_전부_빠지고_더_채울_길이_없으면_추천_없이_풀이_비었다는_안내다() -> None:
    ctx = shown(MILKBREAD, safety=MILK, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("우유식빵")))

    assert result.success
    assert ctx.state.picks == ()
    assert ctx.state.notice is not None
    assert ctx.state.notice.code == "empty"
    assert ctx.state.notice.body == "조건에 맞는 메뉴가 없어요."


async def test_안전_정보를_못_읽었으면_검증하지_않는다() -> None:
    ctx = shown(TOFU)
    ctx.state.safety = None

    with pytest.raises(RuntimeError, match="안전 정보"):
        await propose_meal_candidates(ctx, args(pick("두부조림")))


# ── allergens · items ────────────────────────────────────────────────
async def test_추천의_allergens_는_안전_필터_결과와_같다() -> None:
    ctx = shown(EGGROLL, TOFU, SPINACH, exhausted=True)

    await propose_meal_candidates(ctx, args(pick("계란말이"), pick("두부조림")))

    verdict = filter_food_safety([EGGROLL, TOFU], [], "toddler", months=28)
    by_key = {p.menu_key: p.draft for p in ctx.state.picks}
    assert by_key["계란말이"].allergens == verdict.allergens["계란말이"]
    assert by_key["계란말이"].allergens  # 비어 있지 않다 — 승인할 때 묻는 목록이다
    assert by_key["두부조림"].allergens == verdict.allergens.get("두부조림", ())


# ── reason 문장 규칙 ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "reason",
    [
        "단백질이 30% 들어 있어서 골랐어요",  # 수치
        "아이 몸에 좋아서 골랐어요",  # 몸 상태
        "빈혈이 걱정돼서 골랐어요",  # 진단 · 몸 상태
        "또래보다 잘 먹어서 골랐어요",  # 또래 비교
        "꼭 챙겨 드세요",  # 처방
    ],
)
async def test_reason_에_수치나_몸_상태가_있으면_거절한다(reason: str) -> None:
    ctx = shown(*FULL)

    result = await propose_meal_candidates(ctx, args(pick("두부조림", reason=reason)))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert ctx.state.picks == ()


async def test_reason_이_먹은_식사를_주어로_하지_않아도_통과한다() -> None:
    # 먹은 식사를 주어로 요구하는 규칙은 영양소 분석 요약에만 맞는다
    ctx = shown(*FULL, exhausted=True)

    result = await propose_meal_candidates(
        ctx, args(pick("두부조림", reason="오늘 급식과 겹치지 않아서 골랐어요"))
    )

    assert result.success


# ── 근거 ─────────────────────────────────────────────────────────────
async def test_조회하지_않은_근거를_인용하면_EVIDENCE_REQUIRED_다() -> None:
    ctx = shown(*FULL)

    result = await propose_meal_candidates(ctx, args(pick("두부조림", evidence=[cite(uuid4())])))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
    assert ctx.state.picks == ()


async def test_근거_id_가_UUID_모양이_아니어도_조회하지_않은_근거다() -> None:
    ctx = shown(*FULL)
    bad = EvidenceRef(kind="profile_affinity", id="지어낸-id", note="x")

    result = await propose_meal_candidates(ctx, args(pick("두부조림", evidence=[bad])))

    assert result.error is not None
    assert result.error["code"] == ErrorCode.EVIDENCE_REQUIRED


async def test_근거를_조회했으면_개인화_추천이고_인용_종류는_조회_결과에서_채운다() -> None:
    ctx = shown(*FULL, exhausted=True)
    key = seen_affinity(ctx, "두부")

    # 모델이 kind 를 틀리게 써도 조회 결과의 종류(profile_affinity)를 쓴다
    result = await propose_meal_candidates(
        ctx, args(pick("두부조림", evidence=[cite(key, kind="food_doc", note="두부를 좋아함")]))
    )

    assert result.success
    [only] = ctx.state.picks
    assert only.draft.kind == "personalized"
    [citation] = only.draft.citations
    assert citation.ref == Ref(kind="profile_affinity", id=key)
    assert citation.note == "두부를 좋아함"


async def test_아이_기록_근거가_없으면_일반_추천이고_이유는_코드_문구로_덮는다() -> None:
    ctx = shown(*FULL, exhausted=True)

    await propose_meal_candidates(ctx, args(pick("두부조림", reason="모델이 쓴 이유예요")))

    [only] = ctx.state.picks
    assert only.draft.kind == "general"
    assert only.draft.reason == GENERAL_REASON
    # 아이 기록은 0행이다. 인용한 문서 행에 고른 메뉴 자체가 근거로 붙는다(#442)
    assert [c.ref.kind for c in only.draft.citations] == ["food_doc", "recipe_catalog"]


async def test_인용이_하나도_없어도_고른_메뉴를_근거로_단_일반_추천이다() -> None:
    """#442 — Food 일반 추천은 문서 행이 없어도 고른 메뉴(레시피 카탈로그 행)가 근거다.

    36개월부터는 [예시] 문서 행이 0개라, 문서 행만 근거로 받으면 Food 일반 추천이 나갈 수 없다.
    """
    ctx = shown(*FULL, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("두부조림", evidence=[])))

    assert result.success
    [only] = ctx.state.picks
    assert only.draft.kind == "general"
    [menu] = only.draft.citations
    assert menu.ref == Ref(kind="recipe_catalog", id=menu_evidence_id("mafra_recipe", "두부조림"))
    assert menu.label == "두부조림" and menu.note.strip()
    assert not menu.ref.is_child_record  # 개인화 근거로 세지 않는다


async def test_개인화_추천에는_메뉴_근거를_달지_않는다() -> None:
    ctx = shown(*FULL, exhausted=True)
    key = seen_affinity(ctx, "두부")

    await propose_meal_candidates(ctx, args(pick("두부조림", evidence=[cite(key)])))

    [only] = ctx.state.picks
    assert only.draft.kind == "personalized"
    assert [c.ref.kind for c in only.draft.citations] == ["profile_affinity"]


async def test_레시피_번호가_없는_메뉴는_인용이_없으면_거절한다() -> None:
    """메뉴 근거는 레시피 카탈로그 행을 가리킨다. 레시피 번호가 없는 행(운영 후보 풀에는 없다)은
    가리킬 행이 없어서, 지금처럼 문서 행이 있어야 일반 추천이다."""
    rows = (menu_row("두부조림"), menu_row("시금치나물"), menu_row("미역국"))
    ctx = shown(*rows, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("두부조림", evidence=[])))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "문서 행" in result.error["message"]
    assert ctx.state.picks == ()


async def test_문서_행만_인용하면_일반_추천이고_근거는_food_doc_이다() -> None:
    ctx = shown(*FULL, exhausted=True)
    row = doc_row("질감 올리기")
    ctx.state.seen_docs[row.id] = row

    await propose_meal_candidates(
        ctx, args(pick("두부조림", evidence=[cite(row.id, kind="food_doc", note="질감 안내")]))
    )

    [only] = ctx.state.picks
    assert only.draft.kind == "general"
    doc, menu = only.draft.citations  # 인용한 문서 행 + 고른 메뉴(#442)
    assert (doc.ref.kind, menu.ref.kind) == ("food_doc", "recipe_catalog")
    assert not doc.ref.is_child_record and not menu.ref.is_child_record  # 개인화 근거가 아니다


async def test_오래된_선호만_근거면_일반_추천으로_처리한다() -> None:
    ctx = shown(*FULL, exhausted=True)
    old = seen_affinity(ctx, "두부", days_ago=200)

    await propose_meal_candidates(ctx, args(pick("두부조림", evidence=[cite(old), DOC_CITE])))

    [only] = ctx.state.picks
    assert only.draft.kind == "general"
    assert not any(c.ref.is_child_record for c in only.draft.citations)


async def test_오래된_선호와_최근_근거를_같이_인용하면_개인화다() -> None:
    ctx = shown(*FULL, exhausted=True)
    old = seen_affinity(ctx, "두부", days_ago=200)
    fresh = seen_affinity(ctx, "간장", days_ago=3)

    await propose_meal_candidates(
        ctx, args(pick("두부조림", evidence=[cite(old), cite(fresh, note="간장 간을 좋아함")]))
    )

    [only] = ctx.state.picks
    assert only.draft.kind == "personalized"
    assert len(only.draft.citations) == 2


async def test_기피_근거를_인용했으면_이유에_무엇을_피했는지_있어야_한다() -> None:
    ctx = shown(*FULL, exhausted=True)
    avoided = seen_affinity(ctx, "브로콜리", polarity=-1)

    rejected = await propose_meal_candidates(
        ctx,
        args(
            pick(
                "두부조림",
                reason="오늘 급식과 겹치지 않아서 골랐어요",
                evidence=[cite(avoided, note="브로콜리를 싫어함")],
            )
        ),
    )
    assert not rejected.success
    assert rejected.error is not None
    assert rejected.error["code"] == ErrorCode.INVALID_ARGS

    accepted = await propose_meal_candidates(
        ctx,
        args(
            pick(
                "두부조림",
                reason="브로콜리는 싫어해서 피하고 두부로 골랐어요",
                evidence=[cite(avoided, note="브로콜리를 싫어함")],
            )
        ),
    )
    assert accepted.success


# ── 개수 ─────────────────────────────────────────────────────────────
async def test_더_채울_수_있는데_덜_내면_거절한다() -> None:
    ctx = shown(*FULL, exhausted=False)

    result = await propose_meal_candidates(ctx, args(pick("두부조림")))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "3개" in result.error["message"]
    assert ctx.state.picks == ()


async def test_풀이_모자라_더_채울_길이_없으면_남은_만큼만_내고_안내를_붙인다() -> None:
    ctx = shown(TOFU, EGGROLL, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("두부조림"), pick("계란말이")))

    assert result.success
    assert len(ctx.state.picks) == 2
    assert ctx.state.notice is not None
    assert ctx.state.notice.code == "fewer"


async def test_세_개를_다_채우면_안내가_없다() -> None:
    ctx = shown(*FULL)

    result = await propose_meal_candidates(
        ctx, args(pick("두부조림"), pick("계란말이"), pick("시금치나물"))
    )

    assert result.success
    assert result.data["count"] == 3
    assert ctx.state.notice is None


async def test_결과에_메뉴_이름이나_이유를_싣지_않는다() -> None:
    ctx = shown(*FULL, exhausted=True)

    result = await propose_meal_candidates(ctx, args(pick("두부조림"), pick("시금치나물")))

    assert set(result.data) == {"count", "kinds"}


# ── 이유기 · 오래된 관찰 · 제품 확인 ─────────────────────────────────
async def test_이유기_아이의_일반_추천은_이유기_문구다() -> None:
    weaning = menu_row(
        "감자퓨레", source="mafra_recipe", ingredients=("감자",), stage_min="infant_weaning"
    )
    ctx = shown(weaning, exhausted=True, birth=date(2026, 2, 10))  # 8개월

    await propose_meal_candidates(ctx, args(pick("감자퓨레")))

    [only] = ctx.state.picks
    assert only.draft.kind == "general"
    assert only.draft.reason == READOUTS.get(GENERAL_REASON_WEANING).template


async def test_6개월_넘은_관찰만_근거면_일반_추천이다() -> None:
    # guide_weaning_stage 는 태어난 뒤 전체 관찰을 "먹어 본 재료" 로 돌려준다
    # — 오래된 것도 run state 에 남는다
    ctx = shown(*FULL, exhausted=True)
    key = uuid4()
    ctx.state.seen_evidence[key] = RankedEvidence(
        ref=Ref(kind="observation_food", id=key),
        tier=3,
        label="두부",
        polarity=0,
        observed_on=day(200),
        subject="두부",
    )

    await propose_meal_candidates(
        ctx,
        args(
            pick(
                "두부조림",
                evidence=[cite(key, kind="observation_food", note="두부를 먹어 봄"), DOC_CITE],
            )
        ),
    )

    [only] = ctx.state.picks
    assert only.draft.kind == "general"
    assert not any(c.ref.is_child_record for c in only.draft.citations)


async def test_제품마다_다른_성분_때문에만_막히는_후보도_빠진다() -> None:
    # 밀 알레르기 + 간장(제품마다 밀이 다르다) — 확인 칸(#249)이 생기기 전에는 막는다
    wheat = [SafetyEntry(kind="allergy", label="밀", status="active")]
    bulgogi = menu_row("불고기", source="mafra_recipe", ingredients=("소고기", "간장", "양파"))
    # 비교 후보에는 간장이 없다 — 두부조림은 간장이 들어 같이 걸린다
    ctx = shown(EGGROLL, bulgogi, SPINACH, safety=wheat, exhausted=True)

    await propose_meal_candidates(ctx, args(pick("계란말이"), pick("불고기"), pick("시금치나물")))

    assert ctx.state.blocked_keys == {"불고기"}
    assert [p.menu_key for p in ctx.state.picks] == ["계란말이", "시금치나물"]
