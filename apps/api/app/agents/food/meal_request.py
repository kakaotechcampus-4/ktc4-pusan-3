"""식단 추천 요청 글을 읽는 규칙 — 보호자가 재료 · 요리 종류를 정해 물었는가 (#740).

- 조건 맞는 레시피(`requested_rows` 가 쓴다) — 요청 글에 레시피 이름이나 재료가 나온 것. 모델 앞에서
  코드가 찾는다.
- 모델이 적은 `requested_food` 인정(`said_in_food_request`) — 보호자 말에 그 조건이 있을 때만이다.
  막연한 요청("저녁 추천해줘")에 모델이 조건을 지어 넣으면 근거 없는 일반 추천이 통과한다
  (#597 리뷰).

LLM · DB · 외부 I/O 없음.
"""

from collections.abc import Collection, Iterable, Sequence

from app.agents.common.request_words import normalize_request, request_stems, said_in_request

# 음식을 정한 말이 아니다 — 이것만 남으면 막연한 요청이다
FOOD_GENERIC: frozenset[str] = frozenset(
    (
        "음식 요리 메뉴 반찬 간식 식단 식사 추천 들어간 넣은"
        " 아침 점심 저녁 오늘 내일 아이 애 뭐 것 거"
    ).split()
)

# 이름 · 재료 뒤에 이 말이 오면 그 낱말은 조건이 아니다 — "설탕 적은 간식" 이 설탕 레시피를 끌어오면
# 안 된다. 띄어쓰기를 지운 글에서 낱말 바로 뒤를 본다
_NEGATIONS = ("빼", "뺀", "말고", "없는", "없이", "적게", "적은", "안들어")

# 한 글자 재료("무" · "파")는 오탐이 많다 — 두 글자 이상만 조건으로 본다
MIN_CONDITION_LENGTH = 2


def said_in_food_request(requested_food: str | None, request_texts: Iterable[str]) -> bool:
    """모델이 적은 `requested_food` 가 보호자가 이번 요청에서 한 말에 있는가 (규칙은 공통 함수)."""
    return said_in_request(requested_food, request_texts, generic=FOOD_GENERIC)


def condition_stems(requested_food: str) -> list[str]:
    """`requested_food` 에서 일반 낱말을 뺀 조건 낱말. 후보 이름 · 재료에 있는지 볼 때 쓴다."""
    return [normalize_request(stem) for stem in request_stems(requested_food, generic=FOOD_GENERIC)]


def names_mention(names: Iterable[str], stems: Collection[str]) -> bool:
    """이름 · 재료 중에 조건 낱말이 모두 있는가 (이름 · 재료를 합쳐 본다)."""
    haystack = " ".join(normalize_request(name) for name in names)
    return bool(stems) and all(stem in haystack for stem in stems)


def mentioned_in_request(names: Sequence[str], request_texts: Iterable[str]) -> bool:
    """요청 글에 `names` 중 하나가 조건으로 나왔는가 — 부정 낱말이 바로 뒤에 오면 조건이 아니다."""
    keys = [key for name in names if len(key := normalize_request(name)) >= MIN_CONDITION_LENGTH]
    for text in request_texts:
        said = normalize_request(text)
        for key in keys:
            start = said.find(key)
            while start != -1:
                if not said[start + len(key) :].startswith(_NEGATIONS):
                    return True
                start = said.find(key, start + 1)
    return False
