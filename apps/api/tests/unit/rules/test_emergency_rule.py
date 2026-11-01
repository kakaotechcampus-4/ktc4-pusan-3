"""응급 신호 판정 규칙 — 순수 함수. 사전(yaml)은 읽지 않는다.

여기가 무너지면 두 방향으로 위험하다. 응급을 놓치거나, 아이 상태가 아닌 말에 걸려 도메인 Agent 가
전부 빠진다.
"""

from decimal import Decimal

import pytest

from app.rules.emergency import InfantThreshold, Sign, detect, stated_months
from app.rules.term_match import Term


@pytest.mark.parametrize(
    ("text", "months"),
    [
        ("두 달 된 아기가", (2,)),
        ("생후 50일", (1,)),
        ("2개월 아기", (2,)),
        ("신생아인데", (0,)),
        ("생후 3개월", (3,)),
        ("열한 달 된", (11,)),
        ("6주 된 애기", (1,)),
        ("생후 2개월 된 아기", (2,)),
    ],
)
def test_발화의_월령을_읽는다(text, months):
    assert stated_months(text) == months


@pytest.mark.parametrize("text", ["두 달 전부터 열이 39도", "두 달 동안", "3개월째 기침", "두 살"])
def test_기간_표현은_월령이_아니다(text):
    # 월령으로 읽으면 모든 아이에게 응급 안내가 나간다
    assert stated_months(text) == ()


FEVER = Sign("infant_fever", (Term("fever", ("열이나", "열나", "발열", "고열")),), infant=True)
RULE = InfantThreshold(below_month=3, fever_temperature=Decimal("38.0"))


def test_3개월_미만_발열():
    assert detect("두 달 된 아기가 열이 38도야", [FEVER], RULE) == ("infant_fever",)
    assert detect("신생아인데 열나", [FEVER], RULE) == ("infant_fever",)
    assert detect("두 달 된 아기 열이 37.5도야", [FEVER], RULE) == ()
    assert detect("생후 3개월 아기 열 38도", [FEVER], RULE) == ()
    assert detect("열이 39도야", [FEVER], RULE) == ()  # 월령이 없으면 hook 몫이다


def test_all_of_는_전부_걸려야_한다():
    head = Sign("head_injury", (Term("hit", ("머리를부딪",)), Term("vomit", ("토해", "구토"))))
    assert detect("머리를 부딪혔는데 계속 토해", [head], RULE) == ("head_injury",)
    assert detect("머리를 부딪혔는데 괜찮아", [head], RULE) == ()


def test_guard_가_덮으면_걸리지_않는다():
    seizure = Sign("seizure", (Term("s", ("경련",), guards=("경련얘기", "경련은안")),))
    assert detect("경련을 해", [seizure], RULE) == ("seizure",)
    assert detect("어제 경련 얘기를 들었어", [seizure], RULE) == ()
    assert detect("경련은 안 했어", [seizure], RULE) == ()


def test_걸린_키는_signs_순서다():
    a = Sign("a", (Term("a", ("가나",)),))
    b = Sign("b", (Term("b", ("다라",)),))
    assert detect("다라 가나", [a, b], RULE) == ("a", "b")


def test_all_of_가_비면_걸리지_않는다():
    # 비어 있으면 모든 말에 걸린다 — 사전 검사가 막지만 규칙도 스스로 막는다
    assert detect("두 달 된 아기", [Sign("x", (), infant=True)], RULE) == ()
    assert detect("아무 말", [Sign("y", ())], RULE) == ()


@pytest.mark.parametrize(
    "text",
    [
        "열난 지 3일 된 것 같은데 오늘도 38.5도야",
        "약 먹은 지 2주 된 아이 열이 나네",
        "어린이집 다닌 지 한 달 된 아이인데 열이 나",
        "입원한 지 3일 된 아이 열이 38도",
        "이유식 시작한 지 두 달 된 아기 열나",
    ],
)
def test_한_지_N_된_은_월령이_아니다(text):
    # 3살 아이에게도 119 안내가 나가고 같은 run 의 요청이 말없이 사라진다
    assert stated_months(text) == ()
    assert detect(text, [FEVER], RULE) == ()


def test_과거에_말한_월령은_지금_월령이_아니다():
    # 한 아이의 지금 월령은 하나다 — 가장 큰 값을 본다
    text = "생후 2개월 때 접종했는데 지금 7개월 아기가 열이 나"

    assert stated_months(text) == (2, 7)
    assert detect(text, [FEVER], RULE) == ()


def test_열_말_없는_체온은_걸리지_않는다():
    assert detect("두 달 된 아기 목욕물 38도로 맞췄어", [FEVER], RULE) == ()
    assert detect("생후 50일 아기 목욕물 38도 괜찮아?", [FEVER], RULE) == ()
    assert detect("두 달 된 아기 체온 38도야", [FEVER], RULE) == ("infant_fever",)


@pytest.mark.parametrize(
    "text",
    [
        "열두 달 된 아기가 열이 나요",
        "스물한 달 된 아기가 열이 났어",
        "한 달 아이스크림 끊었더니 열이 났어",
    ],
)
def test_복합_수사와_낱말_조각은_월령이_아니다(text):
    # "열두 달" 의 "두 달" · "스물한 달" 의 "한 달" · "아이스크림" 의 "아이"
    assert stated_months(text) == ()
    assert detect(text, [FEVER], RULE) == ()


@pytest.mark.parametrize(
    "text",
    [
        "생후 2개월 아기 목욕물 38도에서 열심히 놀았어",
        "생후 2개월 아기 분유 38도로 데워서 먹였어(열 안 나)",
    ],
)
def test_열_낱말_조각이나_부정은_체온_경로를_열지_않는다(text):
    assert detect(text, [FEVER], RULE) == ()


def test_열_다음_숫자도_체온_이야기다():
    assert detect("두 달 된 아기 열 38도야", [FEVER], RULE) == ("infant_fever",)
