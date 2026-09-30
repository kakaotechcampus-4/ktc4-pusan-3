"""문서 행 조회 (코드 tool).

모델을 부르기 전에 코드가 부른다. 결과는 프롬프트 [예시]에 들어간다.
지금은 구현부가 주석이라 호출시 NotImplementedError.
"""

from app.agents.activity.store.ports import ActivityDocReader, ActivityDocRow

# 프롬프트 [예시]에 싣는 행 수 (RAG_plan — Activity 5)
DOC_LIMIT = 5


async def search_activity_doc(
    docs: ActivityDocReader, *, months: int, query: str
) -> tuple[ActivityDocRow, ...]:
    """월령 슬라이스 → 의미 검색 상위 DOC_LIMIT 행.

    activity_doc 테이블이 생긴 뒤:
    - 문서 행은 근거(suggestion_evidence)에 넣되 개인화 근거로 세지 않는다 (DocKind).
    - 커버리지 기준은 행 수가 아니라 월령 구간을 빠짐없이 덮는 것이다 (D9).
    """
    raise NotImplementedError("activity_doc 테이블 생성 후 구현")
