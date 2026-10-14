"""음식 이름 두 개가 같은 것을 가리키는지 보는 느슨한 대조. 근거 조회와 후보 가중치가 같이 쓴다.

기록은 "딸기" 처럼 짧게, 모델 · 메뉴 이름은 "딸기우유" 처럼 길게 쓴다. 그래서 한쪽이 다른 쪽에 들어
있으면 같은 것으로 본다. 흔한 한 글자("밥" · "배")가 다른 낱말에 걸리지 않게 짧은 쪽이 두 글자
미만이면 같을 때만 맞는다. 대조 키는 `normalize_menu_key` 를 거친 값이다.
"""

from app.rules.menu_key import normalize_menu_key

# 짧은 쪽의 최소 길이. "밥" 이 "밥새우" 에 걸리지 않게
MIN_NAME_MATCH = 2


def related(a: str, b: str) -> bool:
    """정규화한 이름 둘이 같거나, 짧은 쪽(두 글자 이상)이 긴 쪽에 들어 있으면 참."""
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_NAME_MATCH and short in long


def related_names(a: str, b: str) -> bool:
    """정규화 전 이름 둘을 같은 기준으로 견준다. 비면 거짓이다."""
    key_a, key_b = normalize_menu_key(a), normalize_menu_key(b)
    return bool(key_a) and bool(key_b) and related(key_a, key_b)
