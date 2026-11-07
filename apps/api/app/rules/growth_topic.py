"""Growth 요청의 주제 — 낱말을 문서 행 `area` 키로 읽는다.

"국어 공부를 어려워하는데 어떻게 도와줘야 좋을까?" 는 기록 조각과 요청 조각으로 갈려 요청 조각만
으로는 무엇을 묻는지 모른다. 코드가 두 조각에서 주제를 읽어 [예시] 문서 행을 고르고 기억 조회를
넓히는 데 쓴다. 모델이 정하지 않는다.

    키      `growth_doc.area` 와 같은 영역 키. 사전에 없는 주제(영어)는 `OTHER` — 문서 행이 없다
    판정    `term_match` 의 정규화 · 부분 일치. 조각마다 따로, 띄어쓰기를 넘지 않고 본다
    원문    판정에만 쓰고 돌려주지 않는다 — 문서 쿼리 · 로그에는 키만 간다

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

from collections.abc import Mapping, Sequence

from app.rules.term_match import Term, match_fields, match_spans, match_terms, normalize

# 문서 행에 영역이 없는 주제. 영역 키로는 남기지만 [예시] 행은 없다
OTHER = "other"
# 화면 · 게임 고민. 문서 행에는 이 이름의 영역이 없고 신체운동 · 건강 영역의 `screen` 태그 행이다
SCREEN = "screen"
# 주제 키 → (문서 행 영역, 문서 행 태그). 여기 있는 주제는 그 영역 안에서도 그 태그 행만 근거다
TOPIC_DOC: Mapping[str, tuple[str, str]] = {SCREEN: ("physical_health", "screen")}

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
    # 놀이 이름의 게임(카드 · 보드 · 숫자 게임)과 영상 통화는 화면 고민이 아니다
    Term(
        SCREEN,
        (
            "게임",
            "스마트폰",
            "휴대폰",
            "핸드폰",
            "태블릿",
            "유튜브",
            "영상",
            "동영상",
            "티비",
            "TV",
            "텔레비전",
            "화면",
        ),
        guards=(
            "카드 게임",
            "카드게임",
            "보드 게임",
            "보드게임",
            "숫자 게임",
            "낱말 게임",
            "게임판",
            "영상 통화",
            "영상통화",
            # 디지털이 아닌 놀이 이름. 이름 없이 "게임" 만 말한 고민이 화면 고민이다
            "퍼즐 게임",
            "퍼즐게임",
            "빙고 게임",
            "빙고게임",
            "끝말잇기 게임",
            "끝말잇기게임",
            "스무고개 게임",
            "숨바꼭질 게임",
            "도미노 게임",
            "주사위 게임",
            "짝 맞추기 게임",
            "기억력 게임",
            "몸 게임",
            "말 게임",
            "팀 게임",
        ),
    ),
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
    # 화면 고민의 활동은 화면을 끄거나 약속을 정하는 글이다. 화면 낱말 없이 약속만 써도 맞다
    SCREEN: ("끄고", "끄기", "약속", "끝나", "모래시계", "타이머"),
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


# 질문 문장에 그대로 넣으면 어색한 활용형 낱말. 같은 요청의 다음 주제 낱말을 쓴다
_CONJUGATED = frozenset({"읽어", "말해", "세어", "그려"})


def topic_word(request_texts: Sequence[str], record_texts: Sequence[str]) -> str | None:
    """보호자가 말한 주제 낱말 하나(별칭 그대로). 질문 문장에 넣는다("국어는 집에서 해 본 적이…").

    요청 조각이 먼저고, 요청에 주제가 없을 때만 기록 조각을 읽는다. `TOPIC_TERMS` 순서에서 처음
    걸린 명사 별칭이다 — 활용형(읽어 · 말해 · 세어 · 그려)은 건너뛴다. 기록 조각의 `OTHER`(영어)는
    읽지 않는다(`request_topic` 과 같은 기준). 주제 낱말이 없으면 None 이고 호출부는 묻지 않는다.
    """
    return _first_word(request_texts, skip_other=False) or _first_word(
        record_texts, skip_other=True
    )


def _first_word(texts: Sequence[str], *, skip_other: bool) -> str | None:
    for term in TOPIC_TERMS:
        if skip_other and term.key == OTHER:
            continue
        for alias in term.aliases:
            if alias in _CONJUGATED:
                continue
            single = Term(term.key, (alias,), term.guards)
            if match_fields(tuple(texts), (single,), cross_spaces=False):
                return alias
    return None


def routine_scope(request_texts: Sequence[str], record_texts: Sequence[str]) -> tuple[str, ...]:
    """보호자가 말한 루틴 카테고리 키. 요청 조각이 먼저고, 없을 때만 같은 입력의 기록 조각을 읽는다.

    "밥 먹을 때 힘들어해 / 어떻게 하면 좋을까?" 는 `("mealtime",)` 다. 모델이 기록 있는 다른
    카테고리(손 씻기)를 골라도 이 범위 밖이면 낼 수 없다. 비면 범위를 정하지 않는다.
    """
    return _routine_keys(request_texts) or _routine_keys(record_texts)


def routine_words(request_texts: Sequence[str], record_texts: Sequence[str]) -> tuple[str, ...]:
    """보호자가 말한 루틴 낱말(정규화한 별칭). 요청 조각이 먼저고, 없을 때만 기록 조각을 읽는다.

    "아침에 등원 준비가 너무 오래 걸려" 는 `("등원",)` 이다. `routine_scope` 가 카테고리 키를 주는
    것과 달리 이 낱말은 아이 기록의 이름에 그 낱말이 있는지 볼 때 쓴다 — 행이 없는 루틴 행동을
    교육 활동으로 낼 때 "등원 준비" 기록만 개인화 근거로 세려는 것이다. 판정에만 쓰고 문서 쿼리 ·
    로그에는 보내지 않는다.
    """
    return _routine_spans(request_texts) or _routine_spans(record_texts)


def _routine_spans(texts: Sequence[str]) -> tuple[str, ...]:
    words: list[str] = []
    for text in texts:
        normalized = normalize(text)
        for _, start, end in match_spans(normalized, ROUTINE_TERMS):
            if (word := normalized[start:end]) not in words:
                words.append(word)
    return tuple(words)


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
        Term(term.key, (*term.aliases, *ACTIVITY_TERMS.get(term.key, ())), term.guards)
        for term in TOPIC_TERMS
        if term.key in areas
    )
    return bool(match_fields(texts, terms, cross_spaces=False))


def record_on_topic(names: Sequence[str], areas: Sequence[str]) -> bool:
    """아이 기록의 이름(subject · 라벨)이 주제 영역의 낱말인가. 주제가 없으면 늘 True.

    후보 글을 보는 `on_topic` 과 달리 활동 낱말(`ACTIVITY_TERMS` — 약속 · 규칙 · 끄기 · 순서 …)은
    쓰지 않는다. 그 낱말은 후보 문장에서 주제를 알아보려는 것이라, 기록 이름에 쓰면 "약속 지키기"
    같은 사회 기록이 화면 주제의 근거가 된다.
    """
    if not areas:
        return True
    terms = tuple(term for term in TOPIC_TERMS if term.key in areas)
    return bool(match_fields(tuple(names), terms, cross_spaces=False))


def screen_use(request_texts: Sequence[str], record_texts: Sequence[str]) -> bool:
    """화면 · 게임 고민인데 루틴 행동(양치 · 밥 먹기 …)이 같이 말해지지 않았는가.

    루틴 낱말이 있으면 그 행동이 물은 것이다("양치할 때 유튜브 틀어 줘야 해"). 없으면 루틴 단계를 낼
    사슬이 없으니 호출부가 교육 흐름으로 돌린다. 닫힘(의료 처치 · 증상)과 월령은 호출부가 먼저 본다.
    """
    return SCREEN in request_topic(request_texts, record_texts) and not routine_scope(
        request_texts, record_texts
    )


def doc_areas(topic: Sequence[str]) -> tuple[str, ...]:
    """주제 키를 문서 행 `area` 키로. 화면은 신체운동 · 건강 영역이다. 겹치지 않게 순서대로."""
    return tuple(dict.fromkeys(TOPIC_DOC[key][0] if key in TOPIC_DOC else key for key in topic))


def doc_tags(topic: Sequence[str]) -> tuple[str, ...]:
    """[예시]를 그 태그 행으로 좁히는 태그. 주제가 전부 태그가 있는 주제일 때만이다.

    태그가 없는 주제가 섞이면 좁히지 않는다 — 그 주제의 영역 행이 빠진다.
    """
    if not topic or not all(key in TOPIC_DOC for key in topic):
        return ()
    return tuple(dict.fromkeys(TOPIC_DOC[key][1] for key in topic))


def row_on_topic(area: str | None, tags: Sequence[str], topic: Sequence[str]) -> bool:
    """문서 행(영역 · 태그)이 주제 영역의 행인가. 태그가 있는 주제는 그 태그까지 맞아야 한다."""
    for key in topic:
        doc_area, doc_tag = TOPIC_DOC.get(key, (key, None))
        if area == doc_area and (doc_tag is None or doc_tag in tags):
            return True
    return False


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
