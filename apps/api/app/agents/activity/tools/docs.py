"""문서 행 조회 (코드 tool).

모델을 부르기 전에 코드가 부른다. 결과는 프롬프트 [예시]에 들어간다.
"""

from app.agents.activity.store.ports import ActivityDocReader, ActivityDocRow

# 프롬프트 [예시]에 싣는 행 수 (RAG_plan — Activity 5)
DOC_LIMIT = 5


async def search_activity_doc(
    docs: ActivityDocReader, *, months: int, query: str
) -> tuple[ActivityDocRow, ...]:
    """월령 슬라이스 → 검색 상위 DOC_LIMIT 행.

    - 월령 · 검수(`approved`) 거르기와 검색은 포트가 한다. 지금은 필터만 하고 의미 검색은
      문서가 늘어나면 붙인다. 7행 규모에서는 월령 슬라이스로 충분하다.
    - 0행이면 빈 튜플이다. 실패가 아니다 — 예시 없이 모델을 부른다. 예시는 결을 보여 주는
      것이라 없어도 후보를 낼 수 있다.
    - 포트가 행을 더 주더라도 DOC_LIMIT 에서 자른다. 프롬프트 길이가 여기 걸려 있다.
    - 문서 행은 근거(suggestion_evidence)에 넣되 개인화 근거로 세지 않는다 (DocKind) —
      근거로 다는 것은 run() 이 build() 에 넘길 때 한다.
    """
    rows = await docs.search(months=months, query=query, limit=DOC_LIMIT)
    return tuple(rows[:DOC_LIMIT])
