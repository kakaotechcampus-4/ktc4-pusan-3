"""Food 일반 추천의 메뉴 근거 id — 레시피 카탈로그 행을 늘 같은 uuid 로 가리킨다."""

from uuid import UUID

from app.rules.menu_evidence import MENU_EVIDENCE_KIND, menu_evidence_id


def test_같은_레시피는_늘_같은_id다():
    # Agent 가 근거를 만들 때 · 저장이 있는 메뉴인지 볼 때 · 05 가 이름을 찾을 때 같은 값이어야 한다
    first = menu_evidence_id("mafra_recipe", "7")

    assert isinstance(first, UUID)
    assert first == menu_evidence_id("mafra_recipe", "7")


def test_레시피_번호나_소스가_다르면_다른_id다():
    ids = {
        menu_evidence_id("mafra_recipe", "7"),
        menu_evidence_id("mafra_recipe", "8"),
        menu_evidence_id("mfds_recipe", "7"),
    }

    assert len(ids) == 3


def test_근거_종류는_레시피_카탈로그다():
    assert MENU_EVIDENCE_KIND == "recipe_catalog"
