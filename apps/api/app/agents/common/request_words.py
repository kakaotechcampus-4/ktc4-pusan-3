"""보호자가 이번 요청에서 정한 말이 모델이 적은 조건에 있는가 — Activity · Food 가 같이 쓴다.

모델이 막연한 요청("뭐 하고 놀까?" · "저녁 추천해줘")에 놀이 · 음식을 지어 넣으면 근거 없는
요청 기반 일반 추천이 통과한다(#597 리뷰). 그래서 요청 기반은 보호자 말에 그 조건이 있을 때만
이다. 핵심 규칙은 여기 한 곳이고, 일반 낱말(그것만 남으면 막연한 요청) · 접미사는 Agent 가
넘긴다.

LLM · DB · 외부 I/O 없음. 표준 라이브러리만 쓴다.
"""

import re
import unicodedata
from collections.abc import Collection, Iterable

_NOT_WORD = re.compile(r"[\W_]+")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")
# 일반 낱말 끝의 조사는 떼고 본다 — "주말에" · "실내에서"
_PARTICLES = ("에서", "으로", "에는", "에", "엔", "은", "는", "이", "가", "을", "를", "로", "도")


def normalize_request(text: str) -> str:
    """요청 비교용. NFKC + casefold + 공백 · 문장부호 제거까지만 한다.

    🚨 조사는 떼지 않는다. "색종이 접기" 의 "이" 를 조사로 떼면 "색종접기" 가 되어
    "색종이접기" 와 오히려 갈린다.
    """
    return _NOT_WORD.sub("", unicodedata.normalize("NFKC", text).casefold())


def request_stems(requested: str, *, generic: Collection[str], suffix: str = "") -> list[str]:
    """조건에서 일반 낱말을 빼고 남은 낱말(끝의 `suffix` 는 뗀다). 비면 막연한 요청이다."""
    return [stem for _word, stem in _conditions(requested, generic=generic, suffix=suffix)]


def said_in_request(
    requested: str | None,
    request_texts: Iterable[str],
    *,
    generic: Collection[str],
    suffix: str = "",
) -> bool:
    """모델이 적은 `requested` 가 보호자가 이번 요청에서 한 말에 있는가.

    - 일반 낱말(`generic`)은 빼고 본다 — "추천" · "오늘" 처럼 그것만 남으면 막연한 요청이다
    - 남은 낱말이 **모두** 보호자 말에 있어야 한다 — "실내 블록놀이" 가 "실내" 만 겹쳐서 통과하지
      않게. 보호자가 다른 꼴로 말했으면 놓친다 — 그러면 막연한 요청의 길로 간다
    - 두 글자 이상은 보호자 말 안에 들어 있으면 된다 — "물놀이" ↔ "물놀이 뭐 하지?"
    - 한 글자는 보호자 말의 낱말과 같아야 한다 — "공" 이 "공룡" 에 걸리지 않게
    - 낱말 끝의 `suffix` 를 뗀 줄기로도 본다 — "공놀이" 의 "공" 이 보호자 말에 있으면 같은 요청이다
    """
    if not requested:
        return False
    texts = list(request_texts)
    said = normalize_request(" ".join(texts))
    said_words = {normalize_request(word) for text in texts for word in text.split()}
    conditions = _conditions(requested, generic=generic, suffix=suffix)
    return bool(conditions) and all(
        (len(word) >= 2 and word in said)
        or (len(stem) >= 2 and stem in said)
        or (len(stem) == 1 and stem in said_words)
        for word, stem in conditions
    )


def _conditions(requested: str, *, generic: Collection[str], suffix: str) -> list[tuple[str, str]]:
    return [
        (word, stem)
        for word in _TOKEN.findall(unicodedata.normalize("NFKC", requested).casefold())
        if (stem := word.removesuffix(suffix) if suffix else word)
        and not _is_generic(stem, generic)
    ]


def _is_generic(word: str, generic: Collection[str]) -> bool:
    return any(word.removesuffix(particle) in generic for particle in ("", *_PARTICLES))
