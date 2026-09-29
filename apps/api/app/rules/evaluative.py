"""평가 표현 — 아이를 또래와 비교하거나 발달을 판정하는 말.

진단 · 발달 평가는 만들지 않는다 (루트 CLAUDE.md §1 "안 만드는 것"). 모델이 쓴 문장에 이런
표현이 있으면 그 후보를 거절한다. Activity · Growth · Food 가 같은 목록을 쓴다.

`docs/agents/shared/RAG_plan.md` 의 lint 목록(또래 · 평균 · 정상 · 발달이 · 늦 · 느리 · 뛰어나 ·
재능 · 소질 · 문제 행동)은 사람이 검수하는 문서 행용이다. 한 글자 어간을 모델 문장에 그대로 걸면
"늦은 오후 산책" · "느리게 걷기 놀이" · "산 정상까지 걷기" 가 걸린다. 그래서 여기는 **평가로
쓰일 때의 형태**만 둔다. 공백은 무시하고 부분 일치로 찾는다.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

EVALUATIVE_PHRASES: tuple[str, ...] = (
    # 또래 · 평균과 비교
    "또래보다",
    "또래에 비해",
    "또래 평균",
    "또래 수준",
    "또래만큼",
    "평균보다",
    "평균 이상",
    "평균 이하",
    "평균적",
    # 정상 · 발달 판정
    "정상 발달",
    "정상 범위",
    "정상이에요",
    "정상적",
    "발달이 빠",
    "발달이 느",
    "발달이 늦",
    "발달이 더디",
    "발달 단계",
    "발달 수준",
    "발달 지연",
    "늦된",
    "늦되",
    "늦은 편",
    "느린 편",
    "빠른 편",
    # 능력 평가
    "뛰어난",
    "뛰어나요",
    "뛰어나다",
    "재능",
    "소질",
    "영재",
    "문제 행동",
)


def _dense(text: str) -> str:
    return "".join(text.split())


_DENSE_PHRASES: tuple[tuple[str, str], ...] = tuple((p, _dense(p)) for p in EVALUATIVE_PHRASES)


def find_evaluative(text: str) -> tuple[str, ...]:
    """text 에 들어 있는 평가 표현. 없으면 빈 튜플."""
    dense = _dense(text)
    return tuple(phrase for phrase, needle in _DENSE_PHRASES if needle in dense)
