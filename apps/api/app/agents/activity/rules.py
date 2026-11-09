"""Activity 규칙 값 중 eval 로 흔들 수 있는 것. 안정되면 app/rules/ 로 옮긴다 (설계 3-1).

LLM · DB · 외부 I/O 없음. 표준 라이브러리만 쓴다.
"""

import re
from collections.abc import Iterable, Sequence

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


# 장소마다 있거나 없는 시설 낱말 (#814). 공공데이터에는 시설 칸이 없어서(행안부 기구정보의 76% 는
# "폐쇄형놀이기구 · 기타" 뿐, 11-08 실측) 모델이 장소 이름만 보고 지어낸다 — "가상놀이키즈카페에서
# 볼풀". 미끄럼틀 · 그네처럼 어디에나 있는 기구는 넣지 않는다. 띄어쓰기를 뺀 꼴로 대조한다
FACILITY_WORDS = (
    "볼풀",
    "트램펄린",
    "트램폴린",
    "방방",
    "블록존",
    "레고존",
    "역할놀이존",
    "키즈풀",
    "수영장",
    "물놀이장",
    "클라이밍",
    "암벽",
    "짚라인",
    "편백존",
    "모래놀이장",
    "키즈키친",
)


# 단정하지 않는 말 — "볼풀이 있는지는 확인해 주세요" 는 시설이 있다고 하는 것이 아니다
_UNSURE = ("있는지", "확인", "모르", "다를수", "없을수")
# 부정하는 말 — "볼풀은 싫어해" · "볼풀이 없어서" 는 그곳에 그 시설이 있다는 확인이 아니다
_NEGATED = ("싫", "무서", "말고", "빼고", "없", "않", "안가", "안좋", "별로", "금지", "피해")
_CLAUSE = re.compile(r"[.,!?;·\n]")


def _clauses(text: str) -> list[str]:
    return [part for part in ("".join(c.split()) for c in _CLAUSE.split(text)) if part]


def invented_facilities(
    texts: Sequence[str], *, place_name: str, records: Sequence[tuple[str, int]]
) -> tuple[str, ...]:
    """texts 가 그 장소에 있다고 쓴 시설 중 확인되지 않은 것 (#814 · #820 리뷰).

    - **언급과 확인을 나눈다.** 확인은 부정이 아닌 기록(polarity ≥ 0) 한 문장에 **그 장소 이름과
      시설이 같이** 있고 그 문장이 부정이 아닐 때뿐이다 — "가상놀이키즈카페에서 볼풀 놀이".
      보호자 요청의 시설("볼풀 있는 키즈카페")은 원한다는 말이지 그곳에 있다는 확인이 아니고,
      장소 없이 시설만 나온 기록("볼풀 놀이")도 그 장소의 확인이 아니다.
    - 단정하지 않는 문장("볼풀이 있는지 확인해 주세요")은 시설이 있다고 쓴 것이 아니다.
    """
    claimed = {
        word
        for clause in _clauses(" . ".join(texts))
        if not any(cue in clause for cue in _UNSURE)
        for word in FACILITY_WORDS
        if word in clause
    }
    if not claimed:
        return ()
    place = "".join(place_name.split())
    confirmed = {
        word
        for text, polarity in records
        if polarity >= 0
        for clause in _clauses(text)
        if place and place in clause and not any(cue in clause for cue in _NEGATED)
        for word in FACILITY_WORDS
        if word in clause
    }
    return tuple(word for word in FACILITY_WORDS if word in claimed - confirmed)
