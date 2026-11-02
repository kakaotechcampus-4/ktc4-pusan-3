"""채팅으로 저장하지 않는 알레르기 · 만성질환 이름.

보호자가 프로필에 직접 입력하는 정보다 (루트 §2). 말로 전해도 관찰로 저장하지 않고
직접 입력 안내를 낸다 (#639). 순수 함수 — LLM·DB·외부 I/O 없음.
"""

from app.rules.term_match import Term, match_terms

# 놓치는 말이 보이면 여기에 더한다
_TERMS: tuple[Term, ...] = (
    Term("allergy", ("알레르기", "알러지")),
    Term("atopy", ("아토피",)),
    Term("asthma", ("천식",)),
)


def mentions_safety(text: str) -> bool:
    """text 에 알레르기 · 만성질환 이름이 있는가."""
    return bool(match_terms(text, _TERMS))
