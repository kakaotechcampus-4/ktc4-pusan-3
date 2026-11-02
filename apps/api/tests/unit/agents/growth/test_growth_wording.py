"""평가 표현 회귀 — 금지어 사전 전체를 Growth 출력 10종에 하나씩 넣어 본다 (growth_plan §10).

출력 10종
  교육  ① 제목 ② 단계 ③ 준비물 ④ 이유 ⑤ 근거 note
  루틴  ⑥ 다음 단계 ⑦ 하는 법 ⑧ 이유 ⑨ 근거 note
  코드  ⑩ 코드 상수 문구(growth.readout.yaml) — 성장 추이 서술은 test_growth_delta 가 본다.
        Growth 는 알레르기 확인 문구를 내지 않는다(식품 아닌 알레르기는 대응표로 막는다)
모델이 쓰는 ①~⑨ 에 나오면 출력 검증이 그 후보를 뺀다(재호출 제외 목록으로 — 고쳐서 통과시키지
않는다). ⑩ 에는 처음부터 없어야 한다. 사전은 `app.rules.evaluative`, 문장 모양(또래 비교 · 빠르기
판정 · "늦지 않아요" 같은 부정형)은 tests/unit/rules/test_evaluative.py 와
test_expected T34 에서 왔다.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from app.agents.common.evidence import RankedEvidence
from app.agents.common.refs import Ref
from app.agents.growth.doc_seed import growth_doc_seed
from app.agents.growth.readouts import READOUTS
from app.agents.growth.review import Review, review_learning, review_routine
from app.agents.growth.schemas.learning import LearningItem
from app.agents.growth.schemas.routine import RoutineItem
from app.agents.growth.store.ports import RoutineObservation
from app.agents.growth.tools.safety import load_safety_rules
from app.rules.evaluative import EVALUATIVE_PHRASES, find_evaluative
from tests.unit.agents.growth.support import CHILD, TODAY

RULES = load_safety_rules()
_ROWS = [entry.to_row() for entry in growth_doc_seed() if entry.meta.status == "approved"]
DOCS = {
    row.id: row
    for row in _ROWS
    if row.row_type == "learning_activity" and row.min_month <= 30 < row.max_month
}
DOC_ID = str(next(iter(DOCS)))
STEP = next(row for row in _ROWS if row.doc_key == "growth.routine.self_care.toothbrush.step3")
OBS = RoutineObservation(
    id=UUID(int=4001),
    child_id=CHILD,
    observed_on=TODAY - timedelta(days=1),
    subject="양치하기",
    routine_category="self_care",
    polarity=0,
    assistance_level="partial_assist",
)
SEEN = {
    OBS.id: RankedEvidence(
        ref=Ref(kind="observation_routine", id=OBS.id),
        tier=3,
        label=OBS.subject,
        polarity=0,
        observed_on=OBS.observed_on,
    )
}

# 낱말 사전은 "놀이" 를 붙여 문장 안에 둔다. 모양은 낱말로 못 잡는 것 — 마지막 둘은 아니라고 해도
# 빠르기를 판정한 말이다(test_expected T34 "늦지 않아요 도 실패")
SAMPLES = (
    *(f"{phrase} 놀이" for phrase in EVALUATIVE_PHRASES),
    "또래 아이들보다 잘해요",
    "다른 친구들에 비해 키가 커요",
    "걸음마가 늦어요",
    "말이 조금 느린 것 같아요",
    "키가 작은 편이에요",
    "상위 10% 안에 들어요",
    "성장이 정체됐어요",
    "한글이 늦지 않아요",
    "늦은 게 아니에요",
)

LEARNING_SLOTS = {
    "제목": lambda text: {"title": text},
    "단계": lambda text: {"steps": ["블록을 하나씩 세어 본다", text]},
    "준비물": lambda text: {"materials": ["블록", text]},
    "이유": lambda text: {"reason": text},
    "근거_note": lambda text: {"evidence": [{"id": DOC_ID, "note": text}]},
}
ROUTINE_SLOTS = {
    "다음_단계": lambda text: {"next_step": text},
    "하는_법": lambda text: {"how": text},
    "이유": lambda text: {"reason": text},
    "근거_note": lambda text: {"evidence": [{"id": str(OBS.id), "note": text}]},
}


def learning_item(**override: object) -> LearningItem:
    base = {
        "title": "블록 세기",
        "steps": ["블록을 하나씩 세어 본다"],
        "materials": ["블록"],
        "evidence": [{"id": DOC_ID, "note": "이 나이 예시를 참고했어요"}],
        "reason": "이 나이에 많이 하는 활동이에요",
    }
    return LearningItem.model_validate({**base, **override})


def routine_item(**override: object) -> RoutineItem:
    base = {
        "step_id": str(STEP.id),
        "next_step": "칫솔질 순서를 말로 일러 줘요",
        "how": "거울 앞에서 위 · 아래 · 안쪽 순서를 같이 말해요",
        "evidence": [{"id": str(OBS.id), "note": "어제 어른이 손을 포개 줬어요"}],
        "reason": "지금은 손을 포개 주고 있어서 한 칸 올려요",
    }
    return RoutineItem.model_validate({**base, **override})


def learn(item: LearningItem) -> Review:
    return review_learning(
        [item], months=30, entries=(), rules=RULES, seen={}, docs=DOCS, today=TODAY
    )


def coach(item: RoutineItem) -> Review:
    return review_routine(
        [item],
        rows={STEP.id: STEP},
        capacity=1,
        category="self_care",
        observations={OBS.id: OBS},
        rules=RULES,
        seen=SEEN,
        today=TODAY,
    )


def test_평가_표현이_없으면_교육_후보가_통과한다():
    review = learn(learning_item())
    assert len(review.drafts) == 1 and review.removed == ()


def test_평가_표현이_없으면_루틴_후보가_통과한다():
    review = coach(routine_item())
    assert len(review.drafts) == 1 and review.removed == ()


@pytest.mark.parametrize("slot", sorted(LEARNING_SLOTS))
@pytest.mark.parametrize("text", SAMPLES)
def test_교육_출력_어디에_있어도_그_후보를_뺀다(slot, text):
    review = learn(learning_item(**LEARNING_SLOTS[slot](text)))
    assert review.removed == (0,) and review.drafts == ()


@pytest.mark.parametrize("slot", sorted(ROUTINE_SLOTS))
@pytest.mark.parametrize("text", SAMPLES)
def test_루틴_출력_어디에_있어도_그_후보를_뺀다(slot, text):
    review = coach(routine_item(**ROUTINE_SLOTS[slot](text)))
    assert review.removed == (0,) and review.drafts == ()


@pytest.mark.parametrize("key", sorted(READOUTS.texts))
def test_코드_상수_문구에는_평가_표현이_없다(key):
    text = READOUTS.get(key).template.format(subject="양치하기", metric="키")
    assert find_evaluative(text) == ()


def test_Growth_에는_알레르기_확인_문구가_없다():
    """식품 아닌 알레르기는 대응표로 후보를 막고 문구를 붙이지 않는다(plan1)."""
    assert "caution.non_food_allergy" not in READOUTS.texts
    # 알레르기 정보를 못 불러왔다는 안내(blocked)는 있어도, 등록된 알레르기를 짚는 문구는 없다
    assert not any("등록된 알레르기" in text.template for text in READOUTS.texts.values())
