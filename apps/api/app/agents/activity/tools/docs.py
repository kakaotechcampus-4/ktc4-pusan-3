"""문서 행 조회 (코드 tool).

모델을 부르기 전에 코드가 부른다. 결과는 프롬프트 [예시]에 들어간다.
"""

import hashlib
from collections.abc import Sequence

from app.agents.activity.store.ports import ActivityDocReader, ActivityDocRow
from app.rules.term_match import normalize, split_words

# 프롬프트 [예시]에 싣는 행 수 (RAG_plan — Activity 5)
DOC_LIMIT = 5
# 고를 후보로 포트에서 받는 행 수. 한 월령 구간을 통째로 받는다 — 시드는 구간마다 6–16행이다
DOC_POOL = 50

# 요청 낱말 중 놀이 문장 어디에나 있어 고르는 데 도움이 안 되는 것
_COMMON_WORDS = frozenset({"놀이", "놀자", "하기", "하지", "뭐하", "오늘", "우리", "아이", "같이"})


async def search_activity_doc(
    docs: ActivityDocReader, *, months: int, query: str, rotation: str = ""
) -> tuple[ActivityDocRow, ...]:
    """월령 슬라이스에서 [예시] `DOC_LIMIT` 행을 고른다.

    - 월령 · 검수(`approved`) 거르기는 포트가 한다. 의미 검색은 문서가 늘어나면 붙인다.
    - 고르는 것은 여기서 한다(`pick_examples`). 파일 순서대로 앞의 5행만 주면 같은 월령 아이는
      매번 같은 [예시]를 받고, 모델 추천도 그 행을 따라간다 (#332 라이브 확인).
    - `rotation` 은 run() 이 넘기는 "아이 · 날짜" 다. 같은 날 같은 아이는 같은 [예시]를 받아
      재호출해도 프롬프트가 같고, 날이 바뀌면 바뀐다. 무작위를 쓰지 않아 테스트할 수 있다.
    - 0행이면 빈 튜플이다. 실패가 아니다 — 예시 없이 모델을 부른다.
    - 문서 행은 근거(suggestion_evidence)에 넣되 개인화 근거로 세지 않는다 (DocKind).
    """
    rows = await docs.search(months=months, query=query, limit=DOC_POOL)
    return pick_examples(rows, query=query, rotation=rotation)


def pick_examples(
    rows: Sequence[ActivityDocRow], *, query: str, rotation: str
) -> tuple[ActivityDocRow, ...]:
    """요청과 겹치는 행을 먼저, 남은 자리는 `rotation` 으로 돌린 순서로.

    겹침은 두 방향으로 센다 — 요청 낱말(앞 두 글자)이 제목 · 재료에 들었거나, 행의 재료가
    요청에 들었거나("공놀이" 에 재료 "공").
    """
    stems = _stems(query)
    asked = normalize(query)
    ranked = sorted(rows, key=lambda row: (-_hits(row, stems, asked), _turn(rotation, row.doc_key)))
    return tuple(ranked[:DOC_LIMIT])


def _stems(query: str) -> tuple[str, ...]:
    """요청 낱말의 앞 두 글자. 조사 · 어미가 붙어도 같은 낱말로 본다("종이컵으로" → "종이")."""
    stems = {word[:2] for word in split_words(query) if len(word) >= 2}
    return tuple(sorted(stems - _COMMON_WORDS))


def _hits(row: ActivityDocRow, stems: Sequence[str], asked: str) -> int:
    # 본문은 보지 않는다 — "하고" · "싶어" 같은 요청 낱말이 본문의 동사에 걸린다
    text = normalize(" ".join((row.title, *row.materials)))
    materials = {key for item in row.materials if (key := normalize(item))}
    return sum(1 for stem in stems if stem in text) + sum(1 for m in materials if m in asked)


def _turn(rotation: str, doc_key: str) -> str:
    return hashlib.sha256(f"{rotation}|{doc_key}".encode()).hexdigest()
