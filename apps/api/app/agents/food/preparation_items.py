"""Food 추천의 준비 목록 — 레시피 주재료에서 일정 준비물로 보일 이름만 남긴다.

`suggestion.items` 는 승인한 추천을 일정으로 만들 때 준비물 하나씩이 된다.
그래서 초안을 만드는 한 지점(`meal_review`)에서만 주재료로 줄여 카탈로그 행도 저장소도
공통 `draft_items()` 도 건드리지 않는다.

🚨 이 목록은 화면에 보이는 것만 줄인다. 알레르기 · 연령 판정은 전체 `ingredients` 와
`allergen_codes` 로 계속 한다(`filter_food_safety`) — 준비물에서 빠진 재료도 안전 판정에서는
빠지지 않는다.

제외는 정리 표의 이름과 정확히 같을 때만(`가루` · `기름` · `유` 가 들어 있다고 빼면
밀가루 · 쌀가루 · 빵가루 · 엿기름 · 우유 · 유부 같은 핵심 재료까지 같이 빠짐)
"""

from collections.abc import Sequence

EXCLUDED_NAMES = frozenset(
    {
        # 물
        "물",
        # 소금
        "소금",
        "굵은소금",
        "맛소금",
        "꽃소금",
        "고운소금",
        # 설탕
        "설탕",
        "황설탕",
        "흑설탕",
        "흰설탕",
        # 기름
        "기름",
        "식용유",
        "식물성기름",
        "튀김기름",
        "부침유",
        "샐러드오일",
        "포도씨유",
        "참빛고운포도씨유",
        "올리브유",
        "올리브오일",
        "참기름",
        "들기름",
        "고추기름",
        "고춧기름",
        # 향신 가루
        "후추",
        "후춧가루",
        "통후추",
        "흰후추",
        "흰후춧가루",
        "고춧가루",
        "굵은고춧가루",
        "고운고춧가루",
        "깨소금",
        "파슬리가루",
        "계핏가루",
        "겨자가루",
        "산초가루",
    }
)


def preparation_items(main_ingredients: Sequence[str]) -> tuple[str, ...]:
    """주재료 중 일정 준비물로 표시할 이름만 순서대로 돌려준다.

    입력은 `main_ingredients` 만이다 — 비어 있어도 전체 재료로 되돌아가지 않는다. 앞뒤 공백을
    걷고 빈 이름과 제외 이름은 항목째 뺀다(`""` 로 바꿔 남기지 않는다). 처음 나온 순서를 지키고
    중복은 한 번만이다. 주재료가 없거나 모두 빠지면 `()` 다.
    """
    kept: dict[str, None] = {}
    for raw in main_ingredients:
        name = raw.strip()
        if name and name not in EXCLUDED_NAMES:
            kept.setdefault(name, None)
    return tuple(kept)
