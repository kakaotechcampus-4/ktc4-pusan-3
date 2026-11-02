"""Growth 일반 도서 추천의 책 근거 id — 고른 책의 서지 행을 늘 같은 uuid 로 가리킨다."""

from uuid import UUID

from app.rules.book_evidence import BOOK_EVIDENCE_KIND, book_evidence_id, normalize_isbn


def test_같은_ISBN_은_하이픈_공백이_달라도_같은_id다():
    # Agent 가 근거를 만들 때 · 저장 · 05 가 같은 값을 봐야 한다
    first = book_evidence_id("9788900000001")

    assert isinstance(first, UUID)
    assert first == book_evidence_id("978-89-0000-0001") == book_evidence_id(" 978 890000 0001 ")


def test_ISBN_이_다르면_다른_id다():
    assert book_evidence_id("9788900000001") != book_evidence_id("9788900000002")


def test_메뉴_근거_id_와_겹치지_않는다():
    from app.rules.menu_evidence import menu_evidence_id

    assert book_evidence_id("7") != menu_evidence_id("mafra_recipe", "7")


def test_ISBN_은_하이픈_공백을_지우고_X_는_대문자로():
    assert normalize_isbn(" 89-7044-123-x ") == "897044123X"


def test_근거_종류는_도서_카탈로그다():
    assert BOOK_EVIDENCE_KIND == "book_catalog"
