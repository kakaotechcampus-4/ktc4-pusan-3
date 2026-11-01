"""받침에 맞는 조사. 사용자에게 나가는 한 줄 문구가 "은(는)" 을 그대로 내지 않게 한다.

한글로 끝나지 않는 이름(영문 · 숫자)은 받침을 몰라서 "은(는)" 으로 둔다 — 틀린 조사를 고르느니
그대로 두는 쪽이다. 끝의 공백은 보지 않는다.
"""


def has_final(word: str) -> bool | None:
    """마지막 글자에 받침이 있는가. 한글 음절로 끝나지 않으면 모르니 None."""
    last = word.strip()[-1:]
    if not "가" <= last <= "힣":
        return None
    return (ord(last) - ord("가")) % 28 > 0


def topic_particle(word: str) -> str:
    """받침에 맞는 "은" · "는". 받침을 모르면 "은(는)" 이다."""
    final = has_final(word)
    return "은(는)" if final is None else ("은" if final else "는")
