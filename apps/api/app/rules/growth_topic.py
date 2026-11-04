"""Growth 요청의 주제 — 낱말을 문서 행 `area` 키로 읽는다.

"국어 공부를 어려워하는데 어떻게 도와줘야 좋을까?" 는 기록 조각과 요청 조각으로 갈려 요청 조각만
으로는 무엇을 묻는지 모른다. 코드가 두 조각에서 주제를 읽어 [예시] 문서 행을 고르고 기억 조회를
넓히는 데 쓴다. 모델이 정하지 않는다.

    키      `growth_doc.area` 와 같은 영역 키. 사전에 없는 주제(영어)는 `OTHER` — 문서 행이 없다
    판정    `term_match` 의 정규화 · 부분 일치. 조각마다 따로, 띄어쓰기를 넘지 않고 본다
    원문    판정에만 쓰고 돌려주지 않는다 — 문서 쿼리 · 로그에는 키만 간다

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

from collections.abc import Sequence

from app.rules.term_match import Term, match_fields, match_terms

# 문서 행에 영역이 없는 주제. 영역 키로는 남기지만 [예시] 행은 없다
OTHER = "other"

# 순서가 판정 순서다. 낱말은 "읽어" 같은 활용형까지 적는다 — "그림책 읽어 주기" 가 의사소통이다
TOPIC_TERMS: tuple[Term, ...] = (
    Term(
        "communication",
        (
            "국어",
            "한글",
            "글자",
            "글씨",
            "읽기",
            "읽어",
            "말하기",
            "말해",
            "낱말",
            "이야기",
            "동화",
        ),
    ),
    Term("nature", ("수학", "숫자", "세기", "세어", "셈", "모양", "도형", "과학")),
    Term("art", ("그림", "그려", "그리기", "미술", "색칠", "만들기", "음악", "노래")),
    Term("physical_health", ("몸", "운동", "체육")),
    Term("social", ("친구", "나누기", "차례")),
    Term(OTHER, ("영어", "알파벳")),
)


# 루틴 요청이 어느 카테고리의 말인가. 키는 `observation_routine.routine_category` 와 같다(테스트가
# 대조한다). 낱말은 활용형 앞부분까지 적는다 — "씻" 은 씻기 · 씻어 · 씻겨를 다 잡는다.
# 오탐은 범위를 넓히는 쪽이라 거르는 일이 줄 뿐이다
ROUTINE_TERMS: tuple[Term, ...] = (
    Term("mealtime", ("밥", "식사", "먹", "숟가락", "젓가락", "편식", "반찬")),
    Term(
        "self_care",
        ("씻", "양치", "칫솔", "세수", "목욕", "화장실", "변기", "기저귀", "옷", "신발"),
    ),
    Term("household_task", ("정리", "치우", "청소", "빨래")),
    Term("social_manner", ("인사", "고마워", "미안", "양보")),
    Term("habit", ("빨아", "빠는", "빨기", "손톱", "물어뜯", "코 파", "깨물")),
    Term("transition", ("등원", "하원", "외출", "나갈 준비")),
)


# 후보 글(활동 문장)에서 영역을 알아보는 낱말. 요청에서 주제를 읽는 `TOPIC_TERMS` 보다 넓다 — 활동
# 문장은 "비교하기" · "그림자" 처럼 주제 이름을 그대로 쓰지 않는다. 시드 교육 행이 자기 영역에서
# 통과하는지를 테스트가 본다. 이 표가 있는 영역만 후보 글로 거른다(`strict_topic`) — 낱말이 활동을
# 덮지 못하는 영역(신체 · 사회 · 미술)으로 거르면 맞는 후보까지 빠진다
ACTIVITY_TERMS: dict[str, tuple[str, ...]] = {
    "communication": ("끼적", "이름 부르", "말놀이", "흉내"),
    "nature": (
        "나뭇잎",
        "꽃",
        "날씨",
        "바람",
        "햇볕",
        "그림자",
        "관찰",
        "화분",
        "비교",
        "길이",
        "무게",
        "크기",
        "규칙",
        "패턴",
        "분류",
        "순서",
        "짝 찾",
        "같은 색",
        "같은 것끼리",
        "나눠",
        "쓰임",
        "아래",
        "넣고 꺼내",
        "개수",
    ),
}
HARD_AREAS = frozenset(ACTIVITY_TERMS)


def topic_areas(texts: Sequence[str]) -> tuple[str, ...]:
    """글 조각들에서 걸린 영역 키 전부를 `TOPIC_TERMS` 순서로. 주제가 없으면 빈 튜플.

    조각마다 따로 보고, 낱말 사이 띄어쓰기도 넘지 않는다 — 이어 붙이거나 띄어쓰기를 넘으면
    "수영 어떻게" 가 영어로, "친구들과 학교" 가 과학으로 걸린다.
    """
    found = {
        key for text in texts for key in match_fields((text,), TOPIC_TERMS, cross_spaces=False)
    }
    return tuple(term.key for term in TOPIC_TERMS if term.key in found)


def request_topic(request_texts: Sequence[str], record_texts: Sequence[str]) -> tuple[str, ...]:
    """요청의 주제 영역 키. 요청 조각이 먼저고, 주제가 없을 때만 같은 입력의 기록 조각을 읽는다.

    "오늘 노래 배웠대 / 한글 놀이 추천해줘" 에서 기록 조각의 노래가 요청 주제를 흐리지 않는다.
    기록 조각에서 읽을 때는 `OTHER` 를 뺀다 — "영어 유치원 다니는데 / 집에서 뭐 하면 좋을까?" 의
    영어는 물은 것이 아니라서, 그 때문에 [예시]를 닫거나 거르지 않는다.
    """
    asked = topic_areas(request_texts)
    if asked:
        return asked
    return tuple(area for area in topic_areas(record_texts) if area != OTHER)


def routine_scope(request_texts: Sequence[str], record_texts: Sequence[str]) -> tuple[str, ...]:
    """보호자가 말한 루틴 카테고리 키. 요청 조각이 먼저고, 없을 때만 같은 입력의 기록 조각을 읽는다.

    "밥 먹을 때 힘들어해 / 어떻게 하면 좋을까?" 는 `("mealtime",)` 다. 모델이 기록 있는 다른
    카테고리(손 씻기)를 골라도 이 범위 밖이면 낼 수 없다. 비면 범위를 정하지 않는다.
    """
    return _routine_keys(request_texts) or _routine_keys(record_texts)


def _routine_keys(texts: Sequence[str]) -> tuple[str, ...]:
    found = {
        key for text in texts for key in match_fields((text,), ROUTINE_TERMS, cross_spaces=False)
    }
    return tuple(term.key for term in ROUTINE_TERMS if term.key in found)


def on_topic(texts: Sequence[str], areas: Sequence[str]) -> bool:
    """후보 글(제목 · 단계 · 준비물)에 주제 영역의 낱말이 하나라도 있는가. 주제가 없으면 늘 True.

    영역이 둘 이상이면 어느 영역의 낱말이든 된다. 글마다 따로, 띄어쓰기를 넘지 않고 본다.
    """
    if not areas:
        return True
    terms = tuple(
        Term(term.key, (*term.aliases, *ACTIVITY_TERMS.get(term.key, ())))
        for term in TOPIC_TERMS
        if term.key in areas
    )
    return bool(match_fields(texts, terms, cross_spaces=False))


def strict_topic(request_texts: Sequence[str], record_texts: Sequence[str]) -> tuple[str, ...]:
    """후보 글로 거를 주제 영역 키. 요청 주제(`request_topic`)가 전부 낱말로 덮이는 영역일 때만이다.

    미술 · 신체 · 사회처럼 낱말이 후보 글을 덮지 못하는 영역이 하나라도 섞이면 거르지 않는다 —
    "그림책 읽어 주기" 의 후보가 미술 낱말만 써도 맞는 후보다. 비면 거르지 않는다.
    """
    areas = request_topic(request_texts, record_texts)
    return areas if areas and all(area in HARD_AREAS for area in areas) else ()


def expand_keywords(keywords: Sequence[str]) -> tuple[str, ...]:
    """기억 조회 낱말을 같은 영역의 낱말까지 넓힌다. 원래 낱말이 앞이고 한 번씩만 나온다.

    "국어" 로 `subject=글 읽기` 를 찾게 한다. 영역 밖 낱말은 그대로다.
    """
    wide: list[str] = []
    for keyword in keywords:
        wide.append(keyword)
        for term in TOPIC_TERMS:
            if term.key in match_terms(keyword, TOPIC_TERMS):
                wide.extend(term.aliases)
    return tuple(dict.fromkeys(wide))
