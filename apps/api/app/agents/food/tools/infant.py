"""영아기 tool. 이유식 단계 보조 1개만 registry 에 올린다.

food.md 의 영아기 "분유/수유 기록" · "식재료 도입 기록" 은 쓰기 tool 이다.
기록은 지금 Memory 가 한다 — 쓰기 통로가 둘이면 혼합형 입력에서 같은 기록이 두 번 저장된다.
그래서 아래 두 함수는 스텁으로만 두고 registry 에 올리지 않는다 (열린 결정 2).
"""

from app.agents.common.evidence import RankedEvidence
from app.agents.common.refs import Ref
from app.agents.food.context import FoodContext, require_safety
from app.agents.food.result import ToolResult, ok
from app.agents.food.schemas.infant import GuideWeaningStageArgs, WeaningTopic
from app.agents.food.tools.docs import search_food_doc
from app.rules.age import months_between
from app.rules.menu_key import normalize_menu_key

# 먹어 본 재료로 돌려주는 상한. 이유기 아이가 도입한 재료는 많아야 몇십 가지다 — 너무 줄이면
# 이미 먹은 재료를 새 재료로 권하게 된다
TRIED_LIMIT = 30

_NO_REFERENCE = "자료 없음"
# 주제 → 조회할 문서 종류. 단계 행은 질감 · 먹이는 법, 재료 행은 재료별 도입 안내다
_TOPIC_TYPES: dict[WeaningTopic, tuple[str, ...]] = {
    WeaningTopic.TEXTURE: ("weaning_stage",),
    WeaningTopic.NEW_INGREDIENTS: ("weaning_ingredient",),
    WeaningTopic.GENERAL: ("weaning_stage", "weaning_ingredient"),
}


async def guide_weaning_stage(context: FoodContext, args: GuideWeaningStageArgs) -> ToolResult:
    """개월 수에 맞는 이유식 단계 · 재료 안내 행과, 아이가 이미 먹어 본 재료를 돌려준다.

    - 개월 수는 생일에서 코드가 계산한다. 단계 경계 · 월령별 기준은 문서 행(`food_doc`)의 월령
      (`min_month` 이상 `max_month` 미만)에서 오고, 모델이 만들지 않는다.
    - 문서 행은 `search_food_doc` 이 월령 · 종류로 고르고, 이 아이의 알레르기 · 질환 · 연령 금지에
      걸린 행은 뺀다. 결과에는 출처 기관(`source_org`)을 싣는다. 보여 준 행만 `state.seen_docs` 에
      적는다 — 인용하면 `food_doc` 근거가 되고 개인화로 세지 않는다.
    - 문서에 없는 내용은 `reference="자료 없음"` 이다. 단계나 재료 기준을 지어내지 않는다.
    - 이미 먹어 본 재료는 태어난 뒤 전체의 `observation_food` 에서 같은 재료의 가장 최근 1건만
      싣는다. 새 재료를 권할 때 이미 먹은 것을 빼려는 것이다. 근거로 인용할 수 있게
      `state.seen_evidence` 에도 적는다.
    - 🚨 안전 정보를 못 읽었으면(`None`) 문서를 조회하지 않는다 — 알레르기 행을 거를 수 없다.
    """
    safety = require_safety(context)
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    months = months_between(birth_date, context.today)

    rows = await search_food_doc(
        ports.docs, months=months, row_types=_TOPIC_TYPES[WeaningTopic(args.topic)], safety=safety
    )
    context.state.seen_docs.update({row.id: row for row in rows})

    observed = await ports.memory.observations(
        child_id=context.child_id, date_from=birth_date, date_to=context.today
    )
    tried: dict[str, RankedEvidence] = {}
    for obs in sorted(observed, key=lambda o: o.observed_on, reverse=True):
        key = normalize_menu_key(obs.subject)
        if key and key not in tried and len(tried) < TRIED_LIMIT:
            tried[key] = RankedEvidence(
                ref=Ref(kind="observation_food", id=obs.id),
                tier=3,
                label=obs.subject,
                polarity=obs.polarity,
                observed_on=obs.observed_on,
                subject=obs.subject,
            )
    context.state.seen_evidence.update({item.ref.id: item for item in tried.values()})

    data: dict[str, object] = {
        "docs": [
            {
                "kind": "food_doc",
                "id": str(row.id),
                "row_type": row.row_type,
                "title": row.title,
                "body": row.body,
                "texture": row.texture,
                "source_org": row.source_org,
            }
            for row in rows
        ],
        "tried": [
            {
                "kind": item.ref.kind,
                "id": str(item.ref.id),
                "label": item.label,
                "polarity": item.polarity,
                "observed_on": item.observed_on.isoformat(),
            }
            for item in tried.values()
        ],
    }
    if not rows:
        data["reference"] = _NO_REFERENCE
    return ok("query", "weaning_guide", **data)


# ── TODO: 등록 보류 ─────────────────────────────────────
async def record_feeding(context: FoodContext, args: object) -> ToolResult:
    """분유/수유 기록은 등록 보류.
    기록은 Memory가 observation_food로 한다.
    수유량을 담을 필드를 따로 두지 않고 observation_food.amount에 ml 단위로.
    """
    raise NotImplementedError("등록 보류")


async def record_ingredient_introduction(context: FoodContext, args: object) -> ToolResult:
    """식재료 도입 기록 등록 보류.
    "처음 먹여 봤다"는 관찰이라 Memory가 기록한다. 도입 단계를 담을 필드는 함께 정한다.
    """
    raise NotImplementedError("등록 보류")
