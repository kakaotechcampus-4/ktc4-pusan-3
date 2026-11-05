"""검진 · 접종 기준표 두 장과 로더.

값은 공단 · 질병관리청 자료에서 옮긴 것이다. 해석이 갈리는 칸(기간 끝)은 yaml 머리 주석에 이유를
적고 여기서 값으로 고정한다.
"""

import copy
from decimal import Decimal

import pytest

from app.agents.health.reference import (
    CheckupTable,
    VaccineTable,
    checkup_table,
    condition_codes,
    condition_guidance,
    infant_fever_rule,
    parse_checkup_table,
    parse_code_book,
    parse_condition_guidance,
    parse_infant_fever_rule,
    parse_vaccine_table,
    symptom_codes,
    vaccine_table,
)
from app.rules.term_match import match_terms


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


# ── 증상 · 질환 사전 ──


def _term(code: str, label: str, aliases: list[str], **extra) -> dict:
    return {"code": code, "label": label, "aliases": aliases, **extra}


def _book(*terms: dict) -> dict:
    return {"terms": list(terms)}


def test_label_은_aliases_안에_있다():
    with pytest.raises(ValueError, match="label"):
        parse_code_book(_book(_term("cough", "기침", ["콜록"])), section="symptom_codes.yaml")


def test_코드가_겹치면_거절():
    with pytest.raises(ValueError, match="겹"):
        parse_code_book(
            _book(_term("cough", "기침", ["기침"]), _term("cough", "기침2", ["기침2"])),
            section="symptom_codes.yaml",
        )


def test_코드_사이_별칭_겹침_거절():
    with pytest.raises(ValueError, match="겹"):
        parse_code_book(
            _book(
                _term("cough", "기침", ["기침", "콜록"]),
                _term("cold", "감기", ["감기", "콜 록"]),
            ),
            section="symptom_codes.yaml",
        )


def test_한_글자_별칭_거절():
    with pytest.raises(ValueError, match="한 글자"):
        parse_code_book(_book(_term("fever", "발열", ["발열", "열"])), section="symptom_codes.yaml")


def test_사전은_코드와_화면_이름을_돌려준다():
    book = parse_code_book(
        _book(_term("cough", "기침", ["기침", "콜록"], guards=["기침약"])),
        section="symptom_codes.yaml",
    )

    assert book.labels == {"cough": "기침"}
    assert match_terms("콜록콜록 해요", book.terms) == ("cough",)


def _guidance(**over) -> dict:
    row = {
        "condition": "immunocompromised",
        "symptom": "cough",
        "text": "테스트 안내 문구",
        "source_title": "테스트 출처",
        "publisher": "테스트 기관",
        "url": "https://example.org/guide",
        "license": "KOGL-1",
        "medical_reviewed": True,
        "reviewed_by": "admin",
    }
    return {"rows": [{**row, **over}]}


def test_검수_안_된_행은_로드하지_않는다():
    rows = parse_condition_guidance(_guidance(medical_reviewed=False, reviewed_by=None))

    assert rows == {}


def test_검수된_행은_질환_증상_쌍으로_읽는다():
    rows = parse_condition_guidance(_guidance())

    assert set(rows) == {("immunocompromised", "cough")}
    assert rows[("immunocompromised", "cough")].text == "테스트 안내 문구"


def test_검수했다고_적었으면_검수자가_있어야_한다():
    with pytest.raises(ValueError, match="reviewed_by"):
        parse_condition_guidance(_guidance(reviewed_by=None))


@pytest.mark.parametrize("license_name", ["KOGL-3", "KOGL-4", "CC-BY-NC", ""])
def test_허용_밖_이용조건_거절(license_name):
    with pytest.raises(ValueError, match="license"):
        parse_condition_guidance(_guidance(license=license_name))


@pytest.mark.parametrize("field", ["condition", "symptom"])
def test_사전에_없는_코드를_가리키는_안내_거절(field):
    with pytest.raises(ValueError, match="사전"):
        parse_condition_guidance(_guidance(**{field: "없는_코드"}))


def test_같은_질환_증상_쌍이_두_번이면_거절():
    data = _guidance()
    data["rows"].append(dict(data["rows"][0]))

    with pytest.raises(ValueError, match="겹"):
        parse_condition_guidance(data)


@pytest.mark.parametrize("phrase", ["면역력 저하로 인해 생긴", "감기에 걸린 것 같으면"])
def test_병명_원인_추정_표현이_있으면_거절(phrase):
    with pytest.raises(ValueError, match="추정"):
        parse_condition_guidance(_guidance(text=phrase))


def test_검수_안_된_행도_모양이_틀리면_거절():
    # 검수 전이라 빼는 것이지 검사를 건너뛰는 것이 아니다
    with pytest.raises(ValueError, match="license"):
        parse_condition_guidance(_guidance(medical_reviewed=False, reviewed_by=None, license="x"))


def test_증상_사전이_로드된다():
    book = symptom_codes()

    assert {"fever", "cough", "runny_nose", "vomiting", "diarrhea", "rash", "hives"} <= {
        term.key for term in book.terms
    }
    assert book.labels["fever"] == "발열"
    for text in ("열나요", "미열이 있어요", "38도까지 올랐어요"):
        assert match_terms(text, book.terms) == ("fever",), text


def test_사전은_부정문에서_증상을_뽑지_않는다():
    assert match_terms("콧물이 났는데 열은 없었대", symptom_codes().terms) == ("runny_nose",)
    assert match_terms("열이 없어요", symptom_codes().terms) == ()


def test_질환_사전이_로드된다():
    book = condition_codes()

    assert {"immunocompromised", "asthma"} <= {term.key for term in book.terms}
    assert match_terms("만성 면역력 저하증", book.terms) == ("immunocompromised",)
    assert match_terms("간질환", book.terms) == ()


def test_운영_안내_파일의_행은_전부_로드된다():
    rows = condition_guidance()

    assert set(rows) == {
        ("immunocompromised", "fever"),
        ("immunocompromised", "cough"),
        ("asthma", "cough"),
    }
    # 출처 이용 조건이 허용 목록 안이고, 문구가 비어 있지 않다
    for row in rows.values():
        assert row.license in {"KOGL-0", "KOGL-1", "고시", "CC-BY"}
        assert row.text.strip() and row.url.startswith("https://")


def test_운영_안내_문구는_출처_원문의_말이다():
    # 문구를 고치면 출처와 어긋난다 — 바꾸려면 원문을 다시 확인하고 이 테스트도 함께 고친다
    rows = condition_guidance()

    assert "발열 및 호흡기 증상 있을 시 조기에 의료기관을 방문하여 적절한 진료 필요" in (
        rows[("immunocompromised", "fever")].text
    )
    assert "지속적인 천식치료로 폐기능을 정상으로 유지하는 것이 좋다" in (
        rows[("asthma", "cough")].text
    )


# ── 3개월 미만 발열 응급 기준 (#551) ───────────────────────────────
# 값과 출처 후보 · 검수 상태는 infant_fever.yaml 한 곳이다. 검수 전이어도 규칙은 켠다 — 끄면
# 3개월 미만 발열에 응급 안내가 사라진다. 그래서 값이 비거나 엉뚱하면 기본값 대신 실패한다


def _infant(**over) -> dict:
    data = {
        "below_month": 3,
        "fever_temperature": 38.0,
        "medical_reviewed": False,
        "reviewed_by": None,
        "reviewed_at": None,
        "sources": [{"title": "테스트 출처", "publisher": "테스트 기관", "url": "https://x.org"}],
    }
    return {**data, **over}


def test_응급_기준은_검수_전이어도_로드한다():
    rule = infant_fever_rule()

    assert (rule.below_month, rule.fever_temperature) == (3, Decimal("38.0"))
    assert rule.medical_reviewed is False


def test_응급_기준의_검수_상태를_그대로_싣는다():
    rule = parse_infant_fever_rule(_infant(medical_reviewed=True, reviewed_by="reviewer"))

    assert rule.medical_reviewed is True


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"below_month": None}, "below_month"),
        ({"below_month": 0}, "below_month"),
        ({"below_month": True}, "below_month"),
        ({"fever_temperature": None}, "fever_temperature"),
        ({"fever_temperature": 41.5}, "fever_temperature"),
        ({"medical_reviewed": "no"}, "medical_reviewed"),
        ({"medical_reviewed": True}, "reviewed_by"),
        ({"sources": []}, "출처"),
        ({"sources": [{"title": "x", "publisher": "", "url": "https://x.org"}]}, "빈 칸"),
        ({"sources": [{"title": "x", "publisher": "y", "url": "u", "memo": "z"}]}, "정해지지"),
        ({"threshold_by_site": {}}, "정해지지"),
    ],
)
def test_응급_기준이_엉뚱하면_실패한다(over, message):
    with pytest.raises(ValueError, match=message):
        parse_infant_fever_rule(_infant(**over))
