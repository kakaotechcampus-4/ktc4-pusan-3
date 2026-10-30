"""위험 물질 사전검사 — 아이가 먹으면 안 되는 것을 먹거나 마셨다는 말을 코드로 찾는다 (#626).

위험한 사건을 기록하는 것과 "좋아했다" 를 취향으로 배우는 것은 다른 일이다. 여기서 걸린
입력은 식사 · 취향 기록으로 저장하지 않고 바로 119 에 전화하라는 안내만 낸다.

    마약류              이름만 나와도 건다. "마약" 은 "마약김밥" 같은 음식 이름이 있어서
                        "마약을" · "마약했" 처럼 붙은 꼴만 본다
                        ("마약 먹었어" 는 Supervisor 가 받는다)
    술 · 담배 · 화학제품  같은 마디에서 이름 뒤 INTAKE_WORDS 낱말 안에
                        먹다 · 마시다 · 삼키다가 와야 건다
    삼킨 물건           같은 마디에서 이름 뒤에 삼키다 · 입에 넣다가 와야 건다
                        (동전 · 구슬은 놀잇감이기도 하다)

마디는 문장부호와 "-고 · -서 · -며 · -는데" 로 끝나는 낱말에서 끊는다 — "락스로 닦고 밥 먹었어" 의
"먹었어" 는 락스와 다른 마디다. 걸지 않는 것
    - 부정 · 금지 · 조건: "안 마셨어" · "마시면 안 돼" · "먹지 마"
    - 어른이 주어인 마디: "엄마는 와인 마시고 아이는 주스 마셨어" 의 앞 마디
    - 숟가락을 세는 "술": "밥 두 술 먹었어"
돌려 말한 것("어른 음료를 한 모금")은 여기서 못 잡는다. Supervisor 의 `hazard` 라벨이 받는다 —
되묻기 답으로 와도 같다 (pipeline `_handle_continuation`).
그래도 남는 오탐이 있어 안내는 "아이가 그랬다면" 으로 시작한다.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from typing import Literal

from app.rules.term_match import Term, match_spans, match_terms, split_words

HazardKind = Literal["drug", "alcohol", "tobacco", "chemical", "object"]

# 이름이 든 낱말부터 동사가 든 낱말까지의 거리. "락스를 한 모금 마셨어" 가 3이다
INTAKE_WORDS = 3
# 마디를 나누는 문장부호. `normalize` 가 지우기 전에 원문에서 먼저 자른다
_SENTENCE_BREAK = re.compile(r"[.!?。…\n,;·]+")
# 이렇게 끝나는 낱말 뒤에서 마디가 끊긴다. 글자 하나로 보면 "고기" · "서랍" 에서도 끊겼다
_CLAUSE_ENDINGS = ("고", "서", "며", "는데", "지만", "면서")
# 동사 바로 앞에 오면 하지 않았다는 말이다
_NEGATIONS = ("안", "못")
# 동사 바로 뒤에 오면 조건 · 금지 · 부정이다
# — "마시면 안 돼" · "먹으면" · "먹지 마" · "마시지 않았어"
_NOT_DONE = ("면", "으면", "지않", "지마", "지말", "지못")
# 마디의 주어가 아이가 아닌 어른이다. 이름 앞에서 아이를 가리키는 낱말이 먼저 나오면 아이 얘기다
_ADULTS = (
    "엄마",
    "아빠",
    "할머니",
    "할아버지",
    "어른",
    "어른들",
    "삼촌",
    "이모",
    "고모",
    "남편",
    "아내",
    "와이프",
)
_ADULT_SELF = ("내가", "나는", "나도", "저는", "제가")
_SUBJECT_PARTICLES = ("은", "는", "이", "가", "께서", "도")
_CHILDREN = ("아이", "애", "우리애", "딸", "아들", "아기")

_DRUG = Term(
    "drug",
    (
        "필로폰",
        "히로뽕",
        "메스암페타민",
        "메트암페타민",
        "코카인",
        "헤로인",
        "엑스터시",
        "케타민",
        "펜타닐",
        "대마초",
        "마리화나",
        "마약을",
        "마약했",
        "마약하",
        "마약해",
    ),
)
# 마약 이름은 놀이 · 배움 기록의 대상 이름에도 나오면 안 된다. 예방 교육은 남긴다
_DRUG_NAME = Term(
    "drug",
    (*_DRUG.aliases, "마약"),
    guards=(
        "마약예방",
        "마약퇴치",
        "마약김밥",
        "마약떡볶이",
        "마약옥수수",
        "마약계란",
        "마약달걀",
        "마약토스트",
        "마약빵",
        "마약소스",
        "마약치킨",
        "마약만두",
        "마약핫도그",
        "마약간장",
    ),
)
_ALCOHOL = Term(
    "alcohol",
    ("술", "소주", "맥주", "와인", "막걸리", "양주", "위스키", "보드카", "고량주", "사케"),
    guards=(
        "술래",
        "술빵",
        "술떡",
        "미술",
        "기술",
        "마술",
        "수술",
        "예술",
        "요술",
        "무술",
        "학술",
        "술술",
        "단술",
        "맛술",
        "입술",
        "사케동",
        "술안주",
        "무알콜맥주",
        "무알콜와인",
        # 숟가락 수. 띄어쓰기를 지우고 보므로 "두 술" 도 "두술" 이다
        "한술",
        "두술",
        "세술",
        "네술",
        "몇술",
        "반술",
    ),
)
_TOBACCO = Term("tobacco", ("담배", "담뱃", "꽁초", "니코틴"))
_CHEMICAL = Term(
    "chemical",
    (
        "세제",
        "락스",
        "표백제",
        "세정제",
        "유연제",
        "농약",
        "살충제",
        "제초제",
        "쥐약",
        "모기약",
        "부동액",
        "소독제",
        "방향제",
        "탈취제",
        "아세톤",
        "리무버",
        "휘발유",
        "등유",
        "신너",
        "시너",
    ),
    guards=("세제곱", "등유치원", "무농약", "저농약"),
)
_OBJECT = Term(
    "object",
    (
        "건전지",
        "단추전지",
        "코인전지",
        "배터리",
        "동전",
        "자석",
        "구슬",
        "바둑알",
        "압정",
        "옷핀",
        "비비탄",
        "워터비즈",
        "개구리알",
        "레고",
        "단추",
    ),
)

_INTAKE = Term(
    "intake",
    (
        "먹",
        "마시",
        "마셨",
        "마신",
        "마셔",
        "마심",
        "맛보",
        "맛봤",
        "맛본",
        "맛을보",
        "맛을봤",
        "맛을본",
        "핥",
        "삼키",
        "삼켰",
        "삼킨",
        "삼켜",
        "삼킴",
        "씹",
        "빨아",
        "빨았",
        "피우",
        "피웠",
        "피운",
        "피움",
        "흡입",
        "들이마",
        "입에넣",
        "입에물",
        "입에대",
    ),
    guards=("마시멜로", "먹물", "먹구름"),
)
_SWALLOW = Term("swallow", ("삼키", "삼켰", "삼킨", "삼켜", "삼킴", "입에넣", "입에물", "목에걸"))

_WITH_INTAKE = (_ALCOHOL, _TOBACCO, _CHEMICAL)
_SUBSTANCES = (_DRUG, *_WITH_INTAKE, _OBJECT)
_KINDS: tuple[HazardKind, ...] = ("drug", "alcohol", "tobacco", "chemical", "object")

# 저장 게이트가 관찰 대상 이름에서 보는 것. 물건은 놀잇감 이름이기도 해서 보지 않는다
FOOD_NAME_TERMS = (_DRUG_NAME, _ALCOHOL, _TOBACCO, _CHEMICAL)
PLAY_NAME_TERMS = (_DRUG_NAME,)

_Span = tuple[str, int, int]


def find_hazards(text: str) -> tuple[HazardKind, ...]:
    """글에서 걸린 위험 종류. 순서는 `_KINDS`. 없으면 빈 튜플."""
    found: set[str] = set()
    for words in _clauses(text):
        found.update(_clause_hazards(words))
    return tuple(kind for kind in _KINDS if kind in found)


def hazard_names(names: Sequence[str], terms: Sequence[Term]) -> tuple[str, ...]:
    """관찰 대상 이름 칸(음식 이름 · 활동 · 주제)에서 걸린 위험 종류.

    칸끼리 이어 붙이지 않는다. 칸 안에서는 띄어쓰기를 지우고 본다 — "마약 김밥" 도 guard
    "마약김밥" 에 덮인다.
    """
    hits: set[str] = set()
    for name in names:
        hits.update(match_terms(name, terms))
    return tuple(dict.fromkeys(term.key for term in terms if term.key in hits))


def _clauses(text: str) -> Iterator[list[str]]:
    """마디마다 `normalize` 한 낱말들."""
    for sentence in _SENTENCE_BREAK.split(text):
        clause: list[str] = []
        for word in split_words(sentence):
            clause.append(word)
            if word.endswith(_CLAUSE_ENDINGS) and not match_spans(word, _SUBSTANCES):
                yield clause  # "레고" 처럼 이름이 "고" 로 끝나도 끊지 않는다
                clause = []
        if clause:
            yield clause


def _clause_hazards(words: list[str]) -> set[str]:
    joined = "".join(words)
    starts = [0]
    for word in words[:-1]:
        starts.append(starts[-1] + len(word))

    def word_at(position: int) -> int:
        return bisect_right(starts, position) - 1

    found = {key for key, _, _ in match_spans(joined, (_DRUG,))}
    intake = [span for span in match_spans(joined, (_INTAKE,)) if not _not_done(joined, span)]
    swallow = [span for span in match_spans(joined, (_SWALLOW,)) if not _not_done(joined, span)]
    for terms, verbs in ((_WITH_INTAKE, intake), ((_OBJECT,), swallow)):
        for key, start, end in match_spans(joined, terms):
            name_word = word_at(start)
            if _adult_subject(words[:name_word]):
                continue
            if any(
                verb_start >= end and word_at(verb_start) - word_at(end - 1) <= INTAKE_WORDS
                for _, verb_start, _ in verbs
            ):
                found.add(key)
    return found


def _not_done(joined: str, span: _Span) -> bool:
    """동사가 부정 · 조건 · 금지로 쓰였는가."""
    _, start, end = span
    return joined[max(0, start - 1) : start] in _NEGATIONS or joined[end:].startswith(_NOT_DONE)


def _adult_subject(before: list[str]) -> bool:
    """이름 앞 낱말 중 가장 가까운 주어가 어른인가. 아이를 가리키는 낱말이 먼저 나오면 아니다."""
    for word in reversed(before):
        if word.startswith(_CHILDREN) and word.endswith(_SUBJECT_PARTICLES):
            return False
        if word in _ADULT_SELF:
            return True
        if any(word == adult + particle for adult in _ADULTS for particle in _SUBJECT_PARTICLES):
            return True
    return False
