"""검진 차수 · 권장 접종 계산 — 생일과 오늘만으로, 저장하지 않는다.

기대 값은 test_expected_health.md §7 · §11 이다. 월령은 민법 달력 계산(`months_between`)이다.
"""

from datetime import date, timedelta

import pytest

from app.agents.common.datetime_rules import today_of
from app.agents.health.tools.checkup import (
    compute_checkup_schedule,
    compute_next_vaccines,
    compute_vaccine_schedule,
)
from app.rules.age import date_at_month
from tests.unit.agents.health.support import KST, NOW, TODAY, birth_for


def _round(window):
    return window.round.round if window else None


@pytest.mark.parametrize(
    "months, current, next_",
    [(2, None, 2), (23, 4, 5), (24, 4, 5), (48, 6, 7), (71, 8, None)],
)
def test_차수(months, current, next_):
    status = compute_checkup_schedule(birth_for(months), TODAY)

    assert _round(status.current) == current
    assert _round(status.next) == next_
    assert status.aged_out is False


def test_72개월이면_대상_종료():
    status = compute_checkup_schedule(birth_for(72), TODAY)

    assert status.aged_out is True
    assert status.current is None and status.oral is None and status.next is None


def test_차수는_KST_생일_당일에_바뀐다():
    # NOW 는 UTC 9/27 16:00 = KST 9/28 01:00. 18개월 생일이 KST 로 오늘이다
    birth = birth_for(18, TODAY)

    kst = compute_checkup_schedule(birth, today_of(NOW, KST))
    utc = compute_checkup_schedule(birth, NOW.date())

    assert _round(kst.current) == 4
    assert _round(utc.current) is None  # UTC 날짜로 세면 하루 밀린다 — 그래서 context.today 만 쓴다


def test_기간_사이에는_다음_차수만():
    # 13개월은 3차(9–12)가 끝났고 4차(18–24)는 아직이다 — "기간이 아니에요" 같은 평가 없이 사실만
    status = compute_checkup_schedule(birth_for(13), TODAY)

    assert status.current is None
    assert _round(status.next) == 4


def test_1차는_생후_14일부터_35일까지():
    def at(days):
        return compute_checkup_schedule(TODAY - timedelta(days=days), TODAY)

    assert (_round(at(13).current), _round(at(13).next)) == (None, 1)
    assert (_round(at(14).current), _round(at(14).next)) == (1, 2)
    assert (_round(at(35).current), _round(at(35).next)) == (1, 2)
    assert (_round(at(36).current), _round(at(36).next)) == (None, 2)


def test_생후_2일이면_다음은_1차이고_시작일은_생후_14일이다():
    birth = TODAY - timedelta(days=2)

    status = compute_checkup_schedule(birth, TODAY)

    assert status.current is None
    assert _round(status.next) == 1
    assert status.next.starts_on == birth + timedelta(days=14)
    assert status.next.ends_on == birth + timedelta(days=35)


def test_차수_기간은_last_다음_개월_생일_전날까지다():
    birth = date(2026, 1, 10)  # 2차 4~6개월 → 5월 10일 ~ 8월 9일(7개월 생일 전날)

    inside = compute_checkup_schedule(birth, date(2026, 8, 9))
    after = compute_checkup_schedule(birth, date(2026, 8, 10))

    assert _round(inside.current) == 2
    assert inside.current.starts_on == date(2026, 5, 10)
    assert inside.current.ends_on == date(2026, 8, 9)
    assert after.current is None
    assert _round(after.next) == 3


def test_말일_생일은_그_달_말일로_맞춘다():
    # 10월 31일생의 4개월은 2월 28일이다 (민법 §160③)
    status = compute_checkup_schedule(date(2025, 10, 31), date(2026, 1, 1))

    assert _round(status.next) == 2
    assert status.next.starts_on == date(2026, 2, 28)


def test_구강검진은_같은_시기에만():
    assert compute_checkup_schedule(birth_for(30), TODAY).oral.round.round == 2
    assert compute_checkup_schedule(birth_for(41), TODAY).oral.round.round == 2
    assert compute_checkup_schedule(birth_for(42), TODAY).oral.round.round == 3
    assert compute_checkup_schedule(birth_for(17), TODAY).oral is None
    assert compute_checkup_schedule(birth_for(66), TODAY).oral is None  # 구강은 54~65 가 끝


def _names(months):
    return {(d.vaccine, d.dose) for d in compute_vaccine_schedule(birth_for(months), TODAY)}


@pytest.mark.parametrize(
    "months, expected",
    [
        (
            35,
            {("A형간염", "2차"), ("일본뇌염(불활성화)", "3차"), ("일본뇌염(생백신)", "2차")},
        ),
        (36, set()),
        (47, set()),
        (48, {("DTaP", "5차"), ("IPV", "4차"), ("MMR", "2차")}),
        (71, {("DTaP", "5차"), ("IPV", "4차"), ("MMR", "2차")}),
    ],
)
def test_접종_구간(months, expected):
    # 35/36 경계에서 24~35개월 칸이 빠지고 4~6세 칸은 48개월에 열린다
    assert _names(months) == expected


def test_0개월은_BCG_와_B형간염_1차():
    assert _names(0) == {("BCG", "1회"), ("B형간염", "1차")}


def test_2개월은_기초접종_1차들():
    assert _names(2) == {
        ("DTaP", "1차"),
        ("IPV", "1차"),
        ("Hib", "1차"),
        ("폐렴구균", "1차"),
        ("로타바이러스", "1차"),
    }


def test_72개월에도_만_4_6세_접종과_6세_접종이_이어진다():
    # 검진은 71개월에 끝나지만 접종은 계속된다 — 만 4~6세 칸은 83개월까지다
    assert _names(72) == {
        ("DTaP", "5차"),
        ("IPV", "4차"),
        ("MMR", "2차"),
        ("일본뇌염(불활성화)", "4차"),
    }
    assert _names(83) == _names(72)


def test_84개월부터_11세_전까지는_정해진_접종이_없다():
    assert _names(84) == set()
    assert _names(131) == set()


def test_만_11_12세에는_Tdap_과_HPV_일본뇌염_5차가_열린다():
    assert _names(132) == {("Tdap", "추가")}
    assert _names(143) == {("Tdap", "추가")}
    assert _names(144) == {("Tdap", "추가"), ("HPV", "1차"), ("일본뇌염(불활성화)", "5차")}
    assert _names(155) == _names(144)


def test_156개월부터_표에_정해진_접종이_없다():
    assert compute_vaccine_schedule(birth_for(156), TODAY) == ()
    assert compute_vaccine_schedule(birth_for(200), TODAY) == ()


def test_계산은_표를_바꾸면_따라간다():
    # 표가 정본이다 — 계산식에 값이 박혀 있지 않다
    from app.agents.health.reference import VaccineDose, VaccineTable

    table = VaccineTable(doses=(VaccineDose("테스트", "1회", 10, 10),), year=2099, source="test")

    assert compute_vaccine_schedule(birth_for(10), TODAY, table) == table.doses
    assert compute_vaccine_schedule(birth_for(11), TODAY, table) == ()


def _opens(months, first_month):
    """`months` 개월인 아이가 `first_month` 개월이 되는 날."""
    return date_at_month(birth_for(months), first_month)


def _next(months):
    found = compute_next_vaccines(birth_for(months), TODAY)
    return None if found is None else (found.starts_on, {(d.vaccine, d.dose) for d in found.doses})


def test_다음_접종_시기는_지금_월령_뒤에_처음_열리는_칸이다():
    # 36개월: 24~35개월 칸이 닫혔다. 다음은 48개월(만 4세)에 DTaP 5차 · IPV 4차 · MMR 2차가 열린다
    assert _next(36) == (
        _opens(36, 48),
        {("DTaP", "5차"), ("IPV", "4차"), ("MMR", "2차")},
    )


def test_다음_접종_시기는_시작일을_함께_준다():
    starts_on, doses = _next(0)

    assert starts_on == _opens(0, 1)  # 1개월 생일에 B형간염 2차
    assert doses == {("B형간염", "2차")}


def test_진행_중인_칸이_있어도_다음은_뒤에_열리는_칸이다():
    # 7개월: IPV 3차(6~18)는 지금이고 다음은 12개월(Hib 추가 · 폐렴구균 추가 · MMR 1차 · 수두 …)
    starts_on, doses = _next(7)

    assert starts_on == _opens(7, 12)
    assert ("MMR", "1차") in doses and ("IPV", "3차") not in doses


@pytest.mark.parametrize(
    "months, first_month",
    [(2, 4), (71, 72), (72, 132), (100, 132), (132, 144)],
)
def test_다음_접종_시기_월령(months, first_month):
    assert _next(months)[0] == _opens(months, first_month)


@pytest.mark.parametrize("months", [144, 155, 156, 200])
def test_표_끝_뒤에는_다음_접종이_없다(months):
    assert _next(months) is None


def test_다음_접종은_표를_바꾸면_따라간다():
    from app.agents.health.reference import VaccineDose, VaccineTable

    table = VaccineTable(
        doses=(VaccineDose("가", "1회", 5, 5), VaccineDose("나", "1회", 9, 9)),
        year=2099,
        source="test",
    )

    found = compute_next_vaccines(birth_for(6), TODAY, table)

    assert found is not None and [d.vaccine for d in found.doses] == ["나"]
