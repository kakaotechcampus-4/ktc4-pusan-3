"""추천의 준비물 이름 → 일정 초안 준비물 (#315).

food 여러 건을 한 끼로 묶으면 같은 재료가 겹친다. 화면은 받은 items 를 거르지 않아서
서버가 한 번으로 합친다 (event-draft-flow-v1 §1). 초안은 제출 요청이 받는 모양 안에서만
낸다 — 그대로 제출하면 422 가 나는 초안을 만들지 않는다.
"""

import unicodedata

from app.core.event_draft import MAX_DRAFT_ITEMS, MAX_ITEM_NAME
from app.domains.suggestion.draft_items import draft_items


def names(lists: list[list[str]]) -> list[str]:
    return [item.item_name for item in draft_items(lists)]


def test_저장_전_준비물이라_item_id_는_비어_있다() -> None:
    [item] = draft_items([["수영복"]])
    assert item.item_id is None
    assert item.item_name == "수영복"


def test_처음_나온_자리_순서로_한_번만() -> None:
    assert names([["두부", "애호박"], ["양파", "두부"]]) == ["두부", "애호박", "양파"]


def test_앞뒤_공백을_떼고_같은_이름으로_본다() -> None:
    assert names([["두부"], [" 두부 ", "\t양파"]]) == ["두부", "양파"]


def test_유니코드_정규화가_달라도_같은_이름이다() -> None:
    decomposed = unicodedata.normalize("NFD", "두부")
    assert decomposed != "두부"
    assert names([["두부"], [decomposed]]) == ["두부"]


def test_빈_이름은_버린다() -> None:
    assert names([["", "  ", "물통"]]) == ["물통"]


def test_제출_상한을_넘는_이름은_뺀다() -> None:
    too_long = "가" * (MAX_ITEM_NAME + 1)
    just_fits = "나" * MAX_ITEM_NAME
    assert names([[too_long, just_fits]]) == [just_fits]


def test_제출_상한_개수에서_자른다() -> None:
    many = [f"재료{n}" for n in range(MAX_DRAFT_ITEMS + 5)]
    assert names([many]) == many[:MAX_DRAFT_ITEMS]


def test_준비물이_없으면_빈_목록() -> None:
    assert draft_items([]) == []
    assert draft_items([[], []]) == []
