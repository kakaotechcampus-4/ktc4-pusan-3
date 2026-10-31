"""Activity 규칙 값 중 eval 로 흔들 수 있는 것. 안정되면 app/rules/ 로 옮긴다 (설계 3-1).

LLM · DB · 외부 I/O 없음. 표준 라이브러리만 쓴다.
"""

import re
import unicodedata
from collections.abc import Iterable

# 최근 이 기간 안에 한 활동은 다시 추천하지 않는다. 오늘을 포함한 날 수다 — 7이면 오늘과 앞 6일.
# 잠정값 — eval 로 조정한다
DUPLICATE_WINDOW_DAYS = 7

_NOT_WORD = re.compile(r"[\W_]+")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")

# 한 번의 관찰을 성향처럼 말하는 표현. 근거가 티어 1(confirmed)일 때만 허용한다 (D12)
_OVERSTATING_TOKENS = frozenset({"또", "늘", "항상"})
_OVERSTATING_PREFIXES = ("어제도",)
_OVERSTATING_PHRASES = ("좋아하는", "좋아해")
# requested_play 낱말 끝의 이 말은 떼고 본다 — "공놀이" 의 "공" 이 보호자 말에 있으면 같은 요청이다
_PLAY_SUFFIX = "놀이"


def normalize_activity(text: str) -> str:
    """활동 이름 비교용. NFKC + casefold + 공백 · 문장부호 제거까지만 한다.

    🚨 조사는 떼지 않는다. "색종이 접기" 의 "이" 를 조사로 떼면 "색종접기" 가 되어
    "색종이접기" 와 오히려 갈린다.
    """
    return _NOT_WORD.sub("", unicodedata.normalize("NFKC", text).casefold())


def is_recent_duplicate(content: str, recent: Iterable[str]) -> bool:
    """최근 한 활동과 정규화 후 **완전 일치**하는가.

    유사도 임계값은 쓰지 않는다. 반드시 같아야 하는 "블록쌓기/블럭쌓기" 가 반드시 달라야 하는
    "물놀이/물감놀이" 보다 점수가 낮아서 어떤 임계값도 둘을 가르지 못한다. "레고 조립" 과
    "레고로 자동차 만들기" 는 다른 활동이다.
    """
    key = normalize_activity(content)
    return bool(key) and any(key == normalize_activity(item) for item in recent)


def said_in_request(requested_play: str | None, request_texts: Iterable[str]) -> bool:
    """모델이 적은 `requested_play` 가 보호자가 이번 요청에서 한 말에 있는가.

    모델이 막연한 요청("뭐 하고 놀까?")에 놀이를 지어 넣으면 근거 없는 일반 추천이 통과한다
    (#597 리뷰). 그래서 요청 기반은 보호자 말에 그 놀이가 있을 때만이다. 낱말마다 끝의 "놀이" 를
    떼고 본다("공놀이" → "공").

    - 두 글자 이상은 보호자 말 안에 들어 있으면 된다 — "물놀이" ↔ "물놀이 뭐 하지?",
      "티니핑" ↔ "티니핑으로"
    - 한 글자는 보호자 말의 낱말과 같아야 한다 — "공" 이 "공룡" 에 걸리지 않게
    - "놀이" 만 적은 것은 놀이를 정한 것이 아니다 — 떼고 나면 남는 말이 없다
    """
    if not requested_play:
        return False
    said = normalize_activity(" ".join(request_texts))
    said_words = {normalize_activity(word) for text in request_texts for word in text.split()}
    for word in _TOKEN.findall(unicodedata.normalize("NFKC", requested_play).casefold()):
        stem = word.removesuffix(_PLAY_SUFFIX)
        if not stem:
            continue
        if len(word) >= 2 and word in said:  # "물놀이" ↔ "물놀이 뭐 하지?"
            return True
        if len(stem) >= 2 and stem in said:
            return True
        if len(stem) == 1 and stem in said_words:
            return True
    return False


def overstates(note: str) -> bool:
    """근거 문장이 반복 · 성향 표현을 쓰는가 — 어제도 · 또 · 늘 · 항상 · 좋아하는.

    낱말 단위로 본다. "오늘" 의 "늘" · "또래" 의 "또" 는 걸리지 않는다.
    """
    tokens = _TOKEN.findall(note)
    if any(token in _OVERSTATING_TOKENS for token in tokens):
        return True
    if any(token.startswith(_OVERSTATING_PREFIXES) for token in tokens):
        return True
    dense = "".join(note.split())
    return any(phrase in dense for phrase in _OVERSTATING_PHRASES)
