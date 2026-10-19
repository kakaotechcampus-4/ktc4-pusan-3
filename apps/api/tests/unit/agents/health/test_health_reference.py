"""검진 · 접종 기준표 두 장과 로더.

값은 공단 · 질병관리청 자료에서 옮긴 것이다. 해석이 갈리는 칸(기간 끝)은 yaml 머리 주석에 이유를
적고 여기서 값으로 고정한다.
"""

import copy

import pytest

from app.agents.health.reference import (
    CheckupTable,
    VaccineTable,
    checkup_table,
    parse_checkup_table,
    parse_vaccine_table,
    vaccine_table,
)


def _general(table: CheckupTable):
    return [r for r in table.rounds if r.kind == "general"]


def _oral(table: CheckupTable):
    return [r for r in table.rounds if r.kind == "oral"]


def test_일반_8회_구강_4회():
    table = checkup_table()

    assert [r.round for r in _general(table)] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [r.round for r in _oral(table)] == [1, 2, 3, 4]


def test_일반_검진_기간은_공단_표와_같다():
    table = checkup_table()

    assert [(r.first, r.last) for r in _general(table)] == [
        (14, 35),
        (4, 6),
        (9, 12),
        (18, 24),
        (30, 36),
        (42, 48),
        (54, 60),
        (66, 71),
    ]
    assert [(r.first, r.last) for r in _oral(table)] == [(18, 29), (30, 41), (42, 53), (54, 65)]


def test_1차만_일_단위():
    table = checkup_table()

    assert [r.round for r in table.rounds if r.start == "day"] == [1]
    assert _general(table)[0].start == "day"


def test_차수_기간_끝은_원문_해석대로다():
    # 공단 안내에 끝 날짜 산정 방식이 없어 "개월 말까지(포함)" 로 읽는다 — yaml 머리 주석과 같다.
    # 4차(18–24개월) 는 24개월이 끝나는 날(25개월 생일 전날)까지다
    four = _general(checkup_table())[3]

    assert (four.first, four.last) == (18, 24)


def test_출처와_버전이_있다():
    table = checkup_table()

    assert table.source
    assert table.version


_GOOD = {
    "source": "s",
    "version": "v",
    "fetched_at": "x",
    "general": [{"round": n, "start": "month", "first": n, "last": n + 1} for n in range(1, 9)],
    "oral": [{"round": n, "start": "month", "first": n, "last": n + 1} for n in range(1, 5)],
}


def _with(**changes):
    data = copy.deepcopy(_GOOD)
    data.update(changes)
    return data


def test_파서는_올바른_모양을_받는다():
    assert len(parse_checkup_table(copy.deepcopy(_GOOD)).rounds) == 12


@pytest.mark.parametrize(
    "bad",
    [
        _with(extra=1),
        _with(general=_GOOD["general"][:7]),
        _with(oral=_GOOD["oral"][:3]),
        _with(general=[*_GOOD["general"][:7], {**_GOOD["general"][7], "round": 7}]),
        _with(general=[*_GOOD["general"][:7], {**_GOOD["general"][7], "first": 20, "last": 10}]),
        _with(general=[*_GOOD["general"][:7], {**_GOOD["general"][7], "start": "week"}]),
        _with(general=[*_GOOD["general"][:7], {**_GOOD["general"][7], "memo": "x"}]),
    ],
    ids=[
        "모르는_키",
        "일반_7회",
        "구강_3회",
        "차수_중복",
        "first_가_last_보다_큼",
        "모르는_단위",
        "칸_이름",
    ],
)
def test_모양이_틀리면_ValueError(bad):
    with pytest.raises(ValueError):
        parse_checkup_table(bad)


def test_접종표는_2026년이고_0에서_155개월_만_12세_안이다():
    table = vaccine_table()

    assert table.year == 2026
    assert table.source
    assert table.doses
    assert all(0 <= d.first_month <= d.last_month < 156 for d in table.doses)


def test_접종표에_기초_백신이_들어_있다():
    names = {d.vaccine for d in vaccine_table().doses}

    assert {"BCG", "B형간염", "DTaP", "IPV", "Hib", "폐렴구균", "MMR", "수두", "A형간염"} <= names


def test_접종표_값을_고시_별표_1과_대조한다():
    # 질병관리청 고시 「예방접종의 실시기준 및 방법」 별표 1 의 표준접종시기. 칸 하나가 어긋나면
    # 안내가 한 달 틀어진다
    got = {(d.vaccine, d.dose): (d.first_month, d.last_month) for d in vaccine_table().doses}

    assert got[("BCG", "1회")] == (0, 0)
    assert got[("B형간염", "1차")] == (0, 0)
    assert got[("B형간염", "2차")] == (1, 1)
    assert got[("B형간염", "3차")] == (6, 6)
    assert [got[("DTaP", f"{n}차")] for n in (1, 2, 3, 4)] == [(2, 2), (4, 4), (6, 6), (15, 18)]
    assert got[("DTaP", "5차")] == (48, 83)  # 만 4~6세는 83개월까지다
    assert got[("IPV", "3차")] == (6, 18)
    assert got[("Hib", "추가")] == (12, 15)
    assert got[("폐렴구균", "추가")] == (12, 15)
    assert got[("MMR", "1차")] == (12, 15)
    assert got[("MMR", "2차")] == (48, 83)
    assert got[("IPV", "4차")] == (48, 83)
    assert got[("일본뇌염(불활성화)", "4차")] == (72, 83)  # 만 6세
    assert got[("일본뇌염(불활성화)", "5차")] == (144, 155)  # 만 12세
    assert got[("Tdap", "추가")] == (132, 155)  # 만 11~12세
    assert got[("HPV", "1차")] == (144, 155)  # 만 12세에 시작
    assert got[("수두", "1회")] == (12, 15)
    assert got[("A형간염", "1차")] == (12, 23)


_GOOD_VACCINE = {
    "source": "s",
    "version": "v",
    "fetched_at": "x",
    "year": 2026,
    "doses": [{"vaccine": "BCG", "dose": "1회", "first_month": 0, "last_month": 0}],
}


def test_접종표_파서는_올바른_모양을_받는다():
    table = parse_vaccine_table(copy.deepcopy(_GOOD_VACCINE))

    assert isinstance(table, VaccineTable)
    assert table.doses[0].vaccine == "BCG"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"doses": []},
        {"year": "2026년"},
        {"doses": [{"vaccine": "BCG", "dose": "1회", "first_month": 5, "last_month": 2}]},
        {"doses": [{"vaccine": "BCG", "dose": "1회", "first_month": -1, "last_month": 2}]},
        {"doses": [{"vaccine": "BCG", "dose": "1회", "first_month": 0, "last_month": 156}]},
        {"doses": [{"vaccine": "BCG", "dose": "1회", "first_month": 0}]},
        {
            "doses": [
                {"vaccine": "BCG", "dose": "1회", "first_month": 0, "last_month": 0},
                {"vaccine": "BCG", "dose": "1회", "first_month": 1, "last_month": 1},
            ]
        },
    ],
    ids=[
        "모르는_키",
        "빈_표",
        "연도_모양",
        "first_가_큼",
        "음수",
        "156개월_이상",
        "칸_빠짐",
        "중복",
    ],
)
def test_접종표_모양이_틀리면_ValueError(change):
    data = copy.deepcopy(_GOOD_VACCINE)
    data.update(change)

    with pytest.raises(ValueError):
        parse_vaccine_table(data)


def test_접종표_출처는_고시다():
    # 고시는 저작권법 제7조의 보호받지 못하는 저작물이다. 지침(공공누리 제4유형)은 출처로 안 둔다
    source = vaccine_table().source

    assert "고시" in source and "별표 1" in source
    assert "지침" not in source and "공공누리" not in source


def test_고시가_간격으로만_적은_칸은_계산한_값이다():
    got = {(d.vaccine, d.dose): (d.first_month, d.last_month) for d in vaccine_table().doses}

    # A형간염: 1차 12~23개월 뒤 6~12개월에 2차 → 12+6 ~ 23+12
    assert got[("A형간염", "2차")] == (18, 35)
    # 일본뇌염 생백신: 1차 12~23개월 뒤 12개월에 2차 → 12+12 ~ 23+12
    assert got[("일본뇌염(생백신)", "2차")] == (24, 35)
    # IPV 3차: 표준은 6개월이고 18개월까지 접종 가능하다고 적는다
    assert got[("IPV", "3차")] == (6, 18)
