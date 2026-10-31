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
        ("국어", "한글", "글자", "글씨", "읽기", "읽어", "말하기", "낱말"),
    ),
    Term("nature", ("수학", "숫자", "세기", "셈", "모양", "도형", "과학")),
    Term("art", ("그림", "미술", "색칠", "만들기", "음악", "노래")),
    Term("physical_health", ("몸", "운동", "체육")),
    Term("social", ("친구", "나누기", "차례")),
    Term(OTHER, ("영어", "알파벳")),
)


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
