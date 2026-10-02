"""코드 tool — 모델에게 보이지 않고 코드가 정해진 지점에서 직접 부른다.

모델 tool 로 만들면 모델이 스스로 통과시킬 수 있다 (3-2 "만들지 않는 것").
지금은 구현부가 주석이라 호출시 NotImplementedError.
"""

from collections.abc import Sequence

from app.agents.activity.rules import is_recent_duplicate
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import SafetyEntry


def filter_activity_safety(
    candidates: Sequence[ActivityCandidate],
    *,
    months: int,
    safety: Sequence[SafetyEntry],
) -> tuple[ActivityCandidate, ...]:
    """안전 필터. 통과한 후보만 돌려준다. 걸린 후보는 수정하지 않고 뺀다.

    구현 (#156 의 app/rules/term_match.py 머지 후):
    - `reference/hazard_terms.yaml` 을 로더로 읽는다. 읽지 못하면 예외를 그대로 올린다 —
      빈 사전으로 통과시키면 전 후보가 조용히 나간다.
    - content 와 materials 의 각 항목을 **따로** match_terms 에 넘긴다. 이어 붙이지 않는다.
    - 걸린 축의 block/warn 월령과 대조한다. 0–17개월은 경고도 차단으로 올린다 (D6 규칙 ②).
    - health_safety 는 state='active' 행만 같은 방식으로 대조한다 (D7). 알레르기 별칭은
      Food 와 같은 `reference/allergen_terms.yaml` 을 공통 로더로 읽는다 — food 패키지를
      import 하지 않는다 (docs/agents/README.md §6). 매처도 사전도 하나라 갈라지지 않는다.
    - 로그에는 걸린 건수와 축만 남긴다. 활동명 · 재료 원문은 남기지 않는다.
    """
    raise NotImplementedError("term_match 머지 후 구현")


def filter_recent_duplicates(
    candidates: Sequence[ActivityCandidate], *, recent: Sequence[str]
) -> tuple[ActivityCandidate, ...]:
    """최근 창(`rules.DUPLICATE_WINDOW_DAYS`) 안에 한 활동과 같은 후보를 뺀다.

    판정은 `rules.is_recent_duplicate` — 정규화 후 완전 일치다. 출력 검증(`review.py`)은 같은
    판정으로 후보를 거절하고, 이 함수는 사전 조회한 후보 풀에서 미리 뺄 때 쓴다.
    """
    return tuple(c for c in candidates if not is_recent_duplicate(c.content, recent))
