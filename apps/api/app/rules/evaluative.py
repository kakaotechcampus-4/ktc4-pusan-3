"""평가 표현 — 아이를 또래와 비교하거나 발달을 판정하는 말.

진단 · 발달 평가는 만들지 않는다 (루트 CLAUDE.md §1 "안 만드는 것"). 모델이 쓴 문장에 이런
표현이 있으면 그 후보를 거절한다. Activity · Growth · Food 가 같은 목록을 쓴다.

`docs/agents/shared/RAG_plan.md` 의 lint 목록(또래 · 평균 · 정상 · 발달이 · 늦 · 느리 · 뛰어나 ·
재능 · 소질 · 문제 행동)은 사람이 검수하는 문서 행용이다. 한 글자 어간을 모델 문장에 그대로 걸면
"늦은 오후 산책" · "느리게 걷기 놀이" · "산 정상까지 걷기" 가 걸린다. 그래서 여기는 **평가로
쓰일 때의 형태**만 둔다. 낱말은 공백을 무시하고 부분 일치로 찾고, 낱말로 못 잡는 모양
(또래 비교 · 성장 항목의 빠르기 판정 · "~한 편이에요")은 정규식으로 찾는다.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re

EVALUATIVE_PHRASES: tuple[str, ...] = (
    # 평균과 비교 — 또래 비교는 아래 _COMPARE 가 사이에 낀 말까지 넓게 잡는다
    "평균보다",
    "평균 이상",
    "평균 이하",
    "평균적",
    # 백분위 · 성장곡선 — 놀이 문장에 나올 일이 없는 말이라 낱말 그대로 건다
    "백분위",
    "퍼센타일",
    "성장곡선",
    "성장도표",
    # 정상 · 발달 판정 — 빠르기 판정은 아래 _SPEED 가 잡는다
    "정상 발달",
    "정상 범위",
    "정상이에요",
    "정상적",
    "발달 단계",
    "발달 수준",
    "발달 지연",
    "늦된",
    "늦되",
    # 능력 평가 — 칭찬이어도 능력에 붙이는 꼬리표는 판정이다.
    # "끝까지 해냈어요" 같은 칭찬은 걸리지 않는다
    "뛰어난",
    "뛰어나요",
    "뛰어나다",
    "재능",
    "소질",
    "영재",
    "문제 행동",
)

# 아래 정규식은 낱말 목록으로 못 잡는 모양이다. 고칠 때는 tests/unit/rules/test_evaluative.py 의
# 통과 문장("느린 편지 보내기" · "주말 늦은 오후 산책" 등)이 여전히 통과하는지 같이 본다.

# 또래 · 다른 아이와 비교. 공백을 지운 문장에 건다 — "또래 아이들보다" 처럼 사이에 낀 말까지 잡는다.
# "또래 친구와 공놀이" 는 뒤에 비교 말이 없어 통과한다
_COMPARE = re.compile(
    r"(?:또래|다른(?:아이|친구|애))(?:아이|친구|애)?들?"
    r"(?:보다|에비해|에비하면|와비교|과비교|만큼|평균|수준)"
)
# 상위 10% · 하위 5%. 공백을 지운 문장에 건다
_RANK = re.compile(r"(?:상위|하위)\d+(?:\.\d+)?(?:%|퍼센트|프로)")

# 아래는 공백을 남긴 문장에 건다. 앞 글자가 한글이면 다른 낱말의 끝이라 건너뛴다 ("주말은 늦게")
_SUBJECT = (
    r"(?<![가-힣])(?:말|말하기|걸음마|걷기|뒤집기|발달|성장|발육|키|몸무게|체중|치아|배변|대소변)"
)
# 성장 항목 + 빠르기 판정 — "걸음마가 늦어요" · "말이 빨라요"
# 사이에 정도를 말하는 부사 하나는 낀다 — "말이 조금 느린 것 같아요"
_DEGREE = r"(?:\s*(?:조금|좀|약간|많이|아주|너무|꽤|훨씬|더|좀더))?"
_SPEED = re.compile(
    _SUBJECT
    + r"\s*(?:이|가|은|는|도)"
    + _DEGREE
    + r"\s*(?:빠르|빨라|빠른|빨리|느리|느려|느린|늦|더디|더뎌|더딘)"
)
# 성장 항목 + 정체 · 부족 — "성장이 정체됐어요" · "체중이 부족해요". "재료가 부족해요" 는 통과한다.
# "영양이 부족" 은 넣지 않는다 — Food 가 영양 분석 결과를 말할 때 쓴다
_STALL = re.compile(_SUBJECT + r"\s*(?:이|가|은|는|도)?\s*(?:정체|부족)")
# "작은 편이에요" 처럼 편 뒤에 서술이 와야 판정이다. "느린 편지 보내기" 는 통과한다
_TEND = re.compile(
    r"(?:늦은|느린|빠른|작은|큰|마른|왜소한|더딘)\s*편"
    r"(?:이에요|이예요|입니다|이다|이네요|이라|이고|에\s*속)"
)
_DENSE_PATTERNS = (_COMPARE, _RANK)
_SPACED_PATTERNS = (_SPEED, _STALL, _TEND)


def _dense(text: str) -> str:
    return "".join(text.split())


def _spaced(text: str) -> str:
    return " ".join(text.split())


_DENSE_PHRASES: tuple[tuple[str, str], ...] = tuple((p, _dense(p)) for p in EVALUATIVE_PHRASES)


def find_evaluative(text: str) -> tuple[str, ...]:
    """text 에 들어 있는 평가 표현. 없으면 빈 튜플."""
    dense = _dense(text)
    spaced = _spaced(text)
    found = [phrase for phrase, needle in _DENSE_PHRASES if needle in dense]
    found += [m.group() for pattern in _DENSE_PATTERNS for m in pattern.finditer(dense)]
    found += [m.group() for pattern in _SPACED_PATTERNS for m in pattern.finditer(spaced)]
    return tuple(found)
