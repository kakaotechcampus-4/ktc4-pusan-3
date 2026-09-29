"""Activity 규칙 값 중 eval 로 흔들 수 있는 것. 안정되면 app/rules/ 로 옮긴다 (설계 3-1).

LLM · DB · 외부 I/O 없음. 표준 라이브러리만 쓴다.
"""

import re
import unicodedata
from collections.abc import Iterable

# 최근 이 기간 안에 한 활동은 다시 추천하지 않는다. 잠정값 — eval 로 조정한다
DUPLICATE_WINDOW_DAYS = 7

_NOT_WORD = re.compile(r"[\W_]+")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")

# 한 번의 관찰을 성향처럼 말하는 표현. 근거가 티어 1(confirmed)일 때만 허용한다 (D12)
_OVERSTATING_TOKENS = frozenset({"또", "늘", "항상"})
_OVERSTATING_PREFIXES = ("어제도",)
_OVERSTATING_PHRASES = ("좋아하는", "좋아해")


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
