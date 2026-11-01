"""약 이름 대조. 보호자가 말한 약과 등록된 복약 코스의 이름을 코드가 가른다.

표준 라이브러리만 쓴다(`term_match.normalize` 도 그렇다). 모델에게 묻지 않는다 — 어긋나면 같은
약의 투약이 두 번 남거나(등록 약을 먹인 약 행으로도 적는 경우) 먹인 약 기록이 사라진다(안 겹친
약을 체크 안내로만 돌리는 경우).

띄어쓰기 · 문장부호는 무시하고 한쪽이 다른 쪽을 품으면 같은 약으로 본다. "해열 시럽" 에는 "시럽" 이
같은 약이고 "해열제" 는 다른 약이다.
"""

from app.rules.term_match import normalize

# 이름 없이 "약" 이라고만 말한 표현. 등록된 약이 있으면 그중 하나일 수 있어
# 먹인 약 행으로 쓰지 않는다
GENERIC_MEDICINE: frozenset[str] = frozenset({"약", "그약", "먹는약"})


def same_medicine(said: str, title: str) -> bool:
    """두 이름이 같은 약인가. 한쪽이 비면 거짓이다."""
    left, right = normalize(said), normalize(title)
    return bool(left) and bool(right) and (left in right or right in left)


def mentions_medicine(text: str, name: str) -> bool:
    """문장 안에 이 약 이름이 들어 있는가. 이름이 비면 거짓이다."""
    needle = normalize(name)
    return bool(needle) and needle in normalize(text)


def is_generic_medicine(said: str) -> bool:
    """이름 없이 "약" 이라고만 말했는가."""
    return normalize(said) in GENERIC_MEDICINE
