"""Food 일반 추천의 메뉴 근거 — 고른 메뉴(레시피 카탈로그 행)를 `suggestion_evidence` 로 가리킨다.

Food 의 일반 추천은 문서 행이 없어도 그 메뉴 자체(농식품정보원에서 받아 재료를 검수한 레시피)를
근거로 단다(#442). `recipe_catalog` 의 PK 는 (source, source_id) 문자열이라 UUID 칸인
`suggestion_evidence.source_id` 에 그대로 못 넣어서, 그 둘에서 늘 같은 uuid5 를 만든다.

Agent(근거를 만들 때) · 저장(있는 메뉴인지 볼 때) · 05(화면 이름을 찾을 때)가 이 함수 하나를 쓴다 —
한쪽만 바뀌면 근거가 조용히 사라진다. 세 레이어가 다 부르므로 표준 라이브러리만 쓴다.
"""

from uuid import NAMESPACE_URL, UUID, uuid5

# `suggestion_evidence.source_kind` 값. agents 의 `refs.MenuKind` 와 같은 글자다
MENU_EVIDENCE_KIND = "recipe_catalog"


def menu_evidence_id(source: str, source_id: str) -> UUID:
    """레시피 한 행을 가리키는 근거 id. 같은 (source, source_id) 면 늘 같다."""
    return uuid5(NAMESPACE_URL, f"{MENU_EVIDENCE_KIND}:{source}:{source_id}")
