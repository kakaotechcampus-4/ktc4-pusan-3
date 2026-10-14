"""문서 행 조회 (코드 tool).

모델을 부르기 전에 코드가 부른다. 결과는 프롬프트 [예시] 와 이유식 안내(`guide_weaning_stage`)에
들어간다. 모델이 부르는 tool 이 아니다.
"""

from collections.abc import Sequence

from app.agents.common.age import stage_of
from app.agents.food.doc_seed import ROW_TYPES
from app.agents.food.store.ports import FoodDocReader, FoodDocRow, MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety

# 프롬프트 [예시] · 이유식 안내에 싣는 행 수 (RAG_plan Food 절)
DOC_LIMIT = 3
# 포트에서 받는 후보 수. 알레르기로 빠진 자리를 다음 행이 채우게 거른 뒤에 자른다 —
# 포트가 DOC_LIMIT 만 주면 걸러진 만큼 비어 버린다. 시드가 이 수를 넘으면 의미 검색으로 바꾼다
SEARCH_WINDOW = 50


async def search_food_doc(
    docs: FoodDocReader,
    *,
    months: int,
    row_types: tuple[str, ...],
    safety: Sequence[SafetyEntry],
) -> tuple[FoodDocRow, ...]:
    """월령 슬라이스 → 알레르기로 거른 상위 `DOC_LIMIT` 행.

    - 월령(`min_month` 이상 `max_month` 미만) · `row_type` 으로 거르는 것은 포트가 하고, 여기서도
      한 번 더 본다 — 어댑터가 범위 밖 행을 섞어도 그 월령에 맞지 않는 안내가 나가지 않게.
    - 아이의 알레르기 · 질환 · 연령 금지에 걸리는 행은 뺀다(RAG_plan §3). 행의 `allergen_codes` 와
      제목 · 본문을 메뉴 행처럼 `filter_food_safety` 에 걸어 메뉴와 같은 사전으로 본다. 코드가 빠진
      행도 본문에 이름이 있으면 걸린다. 걸린 행은 이유를 싣지 않고 조용히 뺀다.
    - 지금은 필터만 한다. 의미 검색은 행이 늘면 붙인다 — 시드 53행이라 필터 뒤 몇 행 안 남는다.
    - 0행이면 빈 튜플이다. 실패가 아니다 — 예시 없이 모델을 부른다.
    - 문서 행은 근거(suggestion_evidence)에 넣되 개인화 근거로 세지 않는다.
    """
    if not row_types or not set(row_types) <= ROW_TYPES:
        raise ValueError(f"row_type 이 비었거나 정해진 값이 아니다: {row_types!r}")
    rows = await docs.search(months=months, row_types=row_types, limit=SEARCH_WINDOW)
    in_range = [
        row for row in rows if row.row_type in row_types and row.min_month <= months < row.max_month
    ]
    verdict = filter_food_safety(
        [_as_menu_row(row) for row in in_range], safety, stage_of(months), months=months
    )
    served = {row.menu_key for row in verdict.passed}
    return tuple(row for row in in_range if str(row.id) in served)[:DOC_LIMIT]


def _as_menu_row(row: FoodDocRow) -> MenuCatalogRow:
    """문서 행을 메뉴 행처럼. 제목이 이름, 본문이 재료 자리라 `unchecked` 가 되지 않는다."""
    return MenuCatalogRow(
        menu_key=str(row.id),
        display_name=row.title,
        source="manual",
        resolved=True,
        synced_at=row.written_at,
        ingredients=(row.body,),
        allergen_codes=row.allergen_codes,
    )
