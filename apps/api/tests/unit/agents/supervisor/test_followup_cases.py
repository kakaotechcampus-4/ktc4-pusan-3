"""후속 질문 eval 케이스의 모양 검사 — LLM 을 부르지 않는다.

케이스가 깨져 있으면 라이브 eval 이 크레딧만 쓰고 엉뚱한 숫자를 낸다. 감지 규칙과 채점 함수가
케이스와 맞물리는지를 여기서 본다.
"""

from app.agents.supervisor.followup import looks_like_followup
from app.agents.supervisor.schemas import Segment, SupervisorOutput
from tests.eval.agents.supervisor.followup_cases import (
    FU_BY_ID,
    FU_CASES,
    followup_agent_ok,
    followup_violations,
)

# 이번 문장만 보면 뜻이 통하거나(FU08) 후속 말투가 아닌 증상 · 위험 · 진단 말(FU09~FU11)은 감지와
# 상관없이 앞 대화가 붙었을 때 어떻게 하는지를 보는 케이스다
NOT_DETECTED = {"FU08", "FU09", "FU10", "FU11"}


def _output(*segments: dict) -> SupervisorOutput:
    return SupervisorOutput(segments=[Segment.model_validate(s) for s in segments])


def test_케이스_번호는_겹치지_않고_열네_개다() -> None:
    ids = [case.case_id for case in FU_CASES]
    assert len(ids) == len(set(ids)) == 14


def test_같이_나와야_하는_기록은_이번_입력의_부분_문자열이다() -> None:
    for case in FU_CASES:
        if case.record is not None:
            assert case.record in case.text, case.case_id


def test_후속_케이스는_감지_규칙에_걸린다() -> None:
    for case in FU_CASES:
        if case.case_id not in NOT_DETECTED:
            assert looks_like_followup(case.text), case.case_id


def test_요청을_기대하지_않는_케이스는_agent_채점이_없다() -> None:
    for case in FU_CASES:
        assert (case.agent is None) == (followup_agent_ok(case, None) is None), case.case_id


def test_위반은_C1_표시를_케이스_정의대로_붙인다() -> None:
    request = {
        "text": "그거 먹고 입술이 부었어",
        "kind": "request",
        "agent": "food",
        "food_task": "meal_recommendation",
    }

    (found,) = followup_violations(FU_BY_ID["FU09"], _output(request))

    assert found == "FU09: request 가 나옴 (C1)"
    assert (
        followup_violations(
            FU_BY_ID["FU09"],
            _output({"text": "그거 먹고 입술이 부었어", "kind": "record", "work": "observe"}),
        )
        == []
    )


def test_위험_물질_케이스는_hazard_조각이_없으면_위반이다() -> None:
    record = {"text": "아까 그 물감 조금 먹었어", "kind": "record", "work": "observe"}

    found = followup_violations(FU_BY_ID["FU10"], _output(record))

    assert "FU10: guarded/hazard 가 없음 (C1)" in found
    assert "FU10: record 조각이 나옴 (C1)" in found


def test_앞_추천_이름이_해석에_들면_위반이다() -> None:
    request = {
        "text": "저녁 메뉴 추천해줘",
        "kind": "request",
        "agent": "food",
        "food_task": "meal_recommendation",
        "resolved_request": "블록으로 탑 쌓기 같은 저녁 메뉴 추천",
    }

    assert followup_violations(FU_BY_ID["FU08"], _output(request)) == [
        "FU08: 해석에 앞 추천 이름이 듦"
    ]
