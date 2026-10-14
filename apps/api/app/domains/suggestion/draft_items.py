"""추천의 준비물 이름 → 일정 초안 준비물.

추천 → 일정의 준비물은 `suggestion.items` 에서 온다(값 하나가 `item_name` 하나,
docs/event/event-draft-flow-v1.md §1). food 여러 건을 한 끼로 묶으면 같은 재료가 겹치는데, 화면은
받은 items 를 거르지 않아서 서버가 한 번으로 합친다.

- 앞뒤 공백을 떼고 NFC 로 맞춘 이름이 같으면 같은 준비물이다. 안쪽 띄어쓰기("방울 토마토")는 손대지
  않는다 — 다른 이름을 하나로 합치는 쪽이 더 위험하다.
- 빈 이름은 버린다. 순서는 처음 나온 자리를 지킨다.
- 제출 요청의 상한(이름 `MAX_ITEM_NAME` 자 · `MAX_DRAFT_ITEMS` 개)을 넘는 것은 싣지 않는다.
  초안을 그대로 제출했을 때 422가 나지 않게.
"""

import unicodedata
from collections.abc import Iterable, Sequence

from app.core.event_draft import MAX_DRAFT_ITEMS, MAX_ITEM_NAME, DraftItemPayload


def _clean(name: str) -> str:
    return unicodedata.normalize("NFC", name).strip()


def draft_items(name_lists: Iterable[Sequence[str]]) -> list[DraftItemPayload]:
    """추천마다의 준비물 이름 목록을 초안 준비물 하나로 합친다. 추천 하나면 목록 하나를 넘긴다."""
    seen: dict[str, None] = {}
    for names in name_lists:
        for raw in names:
            name = _clean(raw)
            if name and len(name) <= MAX_ITEM_NAME:
                seen.setdefault(name, None)
    return [DraftItemPayload(item_id=None, item_name=name) for name in list(seen)[:MAX_DRAFT_ITEMS]]
