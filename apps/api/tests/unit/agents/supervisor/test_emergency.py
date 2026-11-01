"""응급 신호 사전(yaml) · 로더 · 안내 — 사전과 규칙이 1:1 로 맞는지 본다.

표현 목록은 H-1 의료 검수 전 가안이다. 여기서 오탐 · 미탐이 나오면 **표현을 고치고** 테스트 기대는
바꾸지 않는다. 걸리지 않아야 할 말이 걸리면 도메인 Agent 가 전부 빠진다.
"""

from copy import deepcopy
from typing import Any

import pytest

from app.agents.common.reference import load_reference
from app.agents.health.readouts import EMERGENCY, READOUTS
from app.agents.supervisor.emergency import (
    EMERGENCY_CODE,
    EMERGENCY_MESSAGE,
    SIGN_KEYS,
    emergency_guidance,
    emergency_signs,
    matched_signs,
    parse_emergency_signs,
)
from app.agents.supervisor.routing import Guidance


def test_사전_키와_규칙_키가_양방향으로_같다():
    keys = [sign.key for sign in emergency_signs()]

    assert sorted(keys) == sorted(SIGN_KEYS) and len(keys) == len(set(keys))


def test_규칙_키는_명세_9절_순서다():
    assert SIGN_KEYS == (
        "breathing",
        "swelling",
        "seizure",
        "infant_fever",
        "head_injury",
        "dehydration",
    )
    assert [sign.key for sign in emergency_signs()] == list(SIGN_KEYS)


POSITIVE = {
    "breathing": "숨을 잘 못 쉬어.",
    "seizure": "경련을 해.",
    "infant_fever": "두 달 된 아기가 열이 38도야.",
    "swelling": "입술이 부었어",
    "head_injury": "머리를 부딪혔는데 계속 토해",
    "dehydration": "소변을 거의 안 보고 입이 바싹 말랐어",
}


def test_모든_키에_걸리는_예시가_있다():
    assert set(POSITIVE) == set(SIGN_KEYS)


@pytest.mark.parametrize(("key", "text"), POSITIVE.items())
def test_신호마다_걸린다(key, text):
    assert emergency_guidance(text) == Guidance(code="emergency", message=EMERGENCY_MESSAGE)
    assert key in matched_signs(text)


@pytest.mark.parametrize(
    "text",
    [
        "어제 경련 얘기를 들었어",
        "경련은 안 했어",
        "숨 잘 쉬고 잘 자",
        "우유 먹고 두드러기 났어.",
        "머리를 부딪혔는데 괜찮아",
        "입이 좀 말라",
        "두 달 전부터 열이 39도",
        "밤 11시에 열 재보니까 38.5도였어.",
    ],
)
def test_아이_상태가_아닌_말은_걸리지_않는다(text):
    assert emergency_guidance(text) is None


def test_응급_문구는_Health_응급_readout_과_같다():
    assert READOUTS.render(EMERGENCY).body == EMERGENCY_MESSAGE
    assert EMERGENCY_CODE == "emergency"


def test_검수_전_행도_켠다():
    # 실제 파일의 행이 전부 medical_reviewed false 여도 6개가 로드된다
    rows = load_reference("emergency_signs.yaml")["signs"]

    assert all(row["medical_reviewed"] is False for row in rows)
    assert len(emergency_signs()) == 6


def _data() -> dict[str, Any]:
    return deepcopy(load_reference("emergency_signs.yaml"))


def test_키가_사전에_없으면_ValueError():
    data = _data()
    data["signs"] = data["signs"][:5]

    with pytest.raises(ValueError, match="키"):
        parse_emergency_signs(data)


def test_모르는_키가_있으면_ValueError():
    data = _data()
    data["signs"][0]["key"] = "unknown"

    with pytest.raises(ValueError, match="키"):
        parse_emergency_signs(data)


def test_행_칸이_빠지면_ValueError():
    data = _data()
    del data["signs"][0]["label"]

    with pytest.raises(ValueError, match="칸"):
        parse_emergency_signs(data)


def test_행에_모르는_칸이_있으면_ValueError():
    data = _data()
    data["signs"][0]["extra"] = 1

    with pytest.raises(ValueError, match="칸"):
        parse_emergency_signs(data)


def test_all_of_가_비면_ValueError():
    data = _data()
    data["signs"][0]["all_of"] = []

    with pytest.raises(ValueError, match="all_of"):
        parse_emergency_signs(data)


def test_별칭이_없는_묶음은_ValueError():
    data = _data()
    data["signs"][0]["all_of"][0]["aliases"] = []

    with pytest.raises(ValueError, match="aliases"):
        parse_emergency_signs(data)


def test_출처가_없으면_ValueError():
    data = _data()
    data["signs"][0]["sources"] = []

    with pytest.raises(ValueError, match="sources"):
        parse_emergency_signs(data)


def test_medical_reviewed_는_불리언이어야_한다():
    data = _data()
    data["signs"][0]["medical_reviewed"] = "false"

    with pytest.raises(ValueError, match="medical_reviewed"):
        parse_emergency_signs(data)


def test_검수했다고_적고_reviewed_by_가_없으면_ValueError():
    data = _data()
    data["signs"][0]["medical_reviewed"] = True

    with pytest.raises(ValueError, match="reviewed_by"):
        parse_emergency_signs(data)


def test_infant_는_발열_행에만_있다():
    data = _data()
    data["signs"][0]["infant"] = True  # breathing

    with pytest.raises(ValueError, match="infant"):
        parse_emergency_signs(data)


def test_발열_행의_infant_가_빠지면_월령_조건이_사라져_ValueError():
    data = _data()
    row = next(row for row in data["signs"] if row["key"] == "infant_fever")
    row["infant"] = False

    with pytest.raises(ValueError, match="infant"):
        parse_emergency_signs(data)


def test_guard_가_자기_별칭을_덮지_않으면_ValueError():
    data = _data()
    data["signs"][0]["all_of"][0]["guards"] = ["아무 상관 없는 말"]

    with pytest.raises(ValueError, match="guard"):
        parse_emergency_signs(data)


def test_여분의_최상위_키는_ValueError():
    data = _data()
    data["extra"] = 1

    with pytest.raises(ValueError, match="extra"):
        parse_emergency_signs(data)


@pytest.mark.parametrize(
    "text",
    [
        "경련 안 해",
        "경련 없었어",
        "경련하지 않았어",
        "발작 없음",
        "숨쉬기 힘들지는 않아",
        "숨쉬기 힘든 줄 알았는데 괜찮아",
        "목이 부어서 소아과 갔어",
        "목이 부었대 편도염이래",
        "얼굴이 부었는데 붓기가 빠졌어",
        "축 늘어지게 낮잠 잤어",
        "발작적으로 기침해",
        "숨이 넘어가게 울어",
        "2개월 아기인데 열나진 않아",
        "열난 지 3일 된 것 같은데 오늘도 38.5도야",
    ],
)
def test_부정_어미와_흔한_말도_걸리지_않는다(text):
    assert emergency_guidance(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "머리를 박고 토했어",
        "머리를 부딪혔는데 두 번 토했어",
        "머리 부딪히고 계속 토해",
        "숨쉬기 힘든 것 같아",
        "숨을 못 쉰다",
        "경기를 일으켰어",
        "생후 2개월 아기 열이 났어",
        "생후 2개월 아기 열났어",
    ],
)
def test_활용형이_달라도_걸린다(text):
    assert emergency_guidance(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "새 간식 먹였는데 알레르기 반응이 없었어",
        "머리 부딪혔는데 토하지 않고 잘 놀아",
        "자고 일어났더니 얼굴이 부었어",
        "모기 물려서 입술이 부었어",
    ],
)
def test_일상_말과_부정은_걸리지_않는다(text):
    assert emergency_guidance(text) is None


def test_같은_자리의_진짜_응급은_여전히_걸린다():
    assert emergency_guidance("입술이 부었어") is not None
    assert emergency_guidance("머리를 부딪혔는데 계속 토해") is not None
