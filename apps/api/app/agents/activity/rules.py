"""Activity 규칙 값 중 eval 로 흔들 수 있는 것. 안정되면 app/rules/ 로 옮긴다 (설계 3-1).

LLM · DB · 외부 I/O 없음. 표준 라이브러리만 쓴다.
"""

import re
from collections.abc import Iterable

from app.agents.common.request_words import normalize_request
from app.agents.common.request_words import said_in_request as common_said_in_request

# 최근 이 기간 안에 한 활동은 다시 추천하지 않는다. 오늘을 포함한 날 수다 — 7이면 오늘과 앞 6일.
# 잠정값 — eval 로 조정한다
DUPLICATE_WINDOW_DAYS = 7

_NOT_WORD = re.compile(r"[\W_]+")
# requested_play 낱말 끝의 이 말은 떼고 본다 — "공놀이" 의 "공" 이 보호자 말에 있으면 같은 요청이다
_PLAY_SUFFIX = "놀이"
# 놀이를 정한 말이 아니다 — requested_play 에서 빼고 남은 낱말로 본다 (#597 리뷰)
_NOT_A_PLAY = frozenset(
    (
        "놀기 놀거리 거리 활동 추천 뭐 것 거"  # 일반 표현
        " 오늘 내일 모레 주말 평일 이번 다음 아침 낮 저녁 밤 오전 오후 방학 자기 전 후 때"  # 때
        " 실내 실외 야외 집 밖 근처 동네"  # 곳
        " 비 날씨 날 오는 더운 추운 맑은 흐린"  # 날씨
    ).split()
)


def normalize_activity(text: str) -> str:
    """활동 이름 비교용. NFKC + casefold + 공백 · 문장부호 제거까지만 한다 (`normalize_request`).

    🚨 조사는 떼지 않는다. "색종이 접기" 의 "이" 를 조사로 떼면 "색종접기" 가 되어
    "색종이접기" 와 오히려 갈린다.
    """
    return normalize_request(text)


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

    핵심 규칙은 `agents/common/request_words` 한 곳에 있다(Food 와 같이 쓴다). Activity 는 놀이가
    아닌 낱말(`_NOT_A_PLAY`)과 끝의 "놀이" 만 넘긴다 — "공놀이" → "공".
    """
    return common_said_in_request(
        requested_play, request_texts, generic=_NOT_A_PLAY, suffix=_PLAY_SUFFIX
    )
