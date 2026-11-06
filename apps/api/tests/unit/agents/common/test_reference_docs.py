"""*_doc 시드 공통 칸 (RAG_plan §1) — 모양이 깨지면 읽을 때 바로 실패한다.

문서 행은 프롬프트 [예시] 로 들어가고 출처를 행 단위로 추적해야 해서, 출처 · 월령 · 검수 칸이
조용히 비면 안 된다.
"""

import copy
from datetime import date

import pytest

from app.agents.common.reference import age_gates
from app.agents.common.reference_docs import (
    CC_BY_URL,
    DOC_MONTH_LIMIT,
    NHS_PUBLIC_ORG,
    OGL_STATEMENT,
    OGL_URL,
    PublicDocSource,
    check_unique_doc_keys,
    parse_doc_meta,
    public_doc_source,
)

ROW = {
    "doc_key": "activity.play.m18_23.cushion_crawl",
    "row_type": "play_idea",
    "title": "쿠션 길 기어 넘기",
    "body": "쿠션을 늘어놓고\n  기어서 넘어오게 한다.",
    "min_month": 18,
    "max_month": 24,
    "tags": ["gross_motor"],
    "source_title": "제4차 어린이집 표준보육과정",
    "source_org": "보건복지부",
    "source_year": 2020,
    "source_locator": "0~1세 신체운동 · 신체활동 즐기기",
    "license_basis": "public_law",
    "status": "draft",
    "authored_by": "03leedo",
    "version": 1,
    "setting": "indoor",  # 도메인 칸 — 공통 검사는 domain_keys 로 통과시킨다
}


def parse(row):
    return parse_doc_meta(
        row,
        file="t.yaml",
        key_prefix="activity.",
        row_types=frozenset({"play_idea"}),
        domain_keys=frozenset({"setting"}),
    )


def broken(**change):
    row = copy.deepcopy(ROW)
    for key, value in change.items():
        if value is ...:
            row.pop(key)
        else:
            row[key] = value
    return row


class TestValid:
    def test_월령은_이상_미만이다(self):
        meta = parse(ROW)
        assert meta.covers(18) and meta.covers(23)
        assert not meta.covers(24)

    def test_본문_줄바꿈을_한_칸으로_모은다(self):
        assert parse(ROW).body == "쿠션을 늘어놓고 기어서 넘어오게 한다."

    def test_search_text_는_title_과_tags_로_만든다(self):
        assert parse(ROW).search_text == "쿠션 길 기어 넘기 gross_motor"

    @pytest.mark.parametrize("basis", ["cc_by", "ogl_3", "pdl_1", "cc0"])
    def test_공개_라이선스_네_값이_읽힌다(self, basis):
        """CC BY · OGL v3.0 · PDL 1.0 · CC0. 값 계약에 두는 것과 행이 있는 것은 별개다 (#510)."""
        assert parse(broken(license_basis=basis)).license_basis == basis

    def test_approved_는_검수자와_검수일이_있으면_통과(self):
        meta = parse(
            broken(status="approved", reviewed_by="nnhhlee", reviewed_at=date(2026, 10, 7))
        )
        assert meta.status == "approved"


class TestBroken:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"extra": 1}, "칸"),
            ({"source_locator": ...}, "source_locator"),
            ({"source_org": "  "}, "source_org"),
            ({"doc_key": "food.x"}, "시작"),
            ({"row_type": "outing"}, "row_type"),
            ({"max_month": None}, "비울 수 없다"),
            ({"max_month": 18}, "min"),
            ({"max_month": 73}, "min"),
            ({"min_month": "18"}, "정수"),
            ({"source_year": "2020"}, "source_year"),
            ({"license_basis": "kogl_5"}, "license_basis"),
            ({"license_basis": "cc_by_sa"}, "license_basis"),
            ({"license_basis": "ogl_2"}, "license_basis"),
            ({"status": "done"}, "status"),
            ({"reviewed_by": "03leedo"}, "같다"),
            ({"status": "approved", "reviewed_by": "nnhhlee"}, "검수일"),
            ({"tags": "gross_motor"}, "tags"),
            ({"version": 0}, "version"),
        ],
    )
    def test_칸이_깨지면_실패한다(self, change, message):
        with pytest.raises(ValueError, match=message):
            parse(broken(**change))

    @pytest.mark.parametrize("reviewer", [" 03leedo", "03leedo ", "	03leedo"])
    def test_공백만_다른_같은_사람도_작성자와_검수자가_같다고_본다(self, reviewer):
        """작성자 · 검수자는 GitHub 아이디로 적는다 (RAG_plan §1). 비교 전에 앞뒤 공백을 지운다."""
        with pytest.raises(ValueError, match="같다"):
            parse(broken(reviewed_by=reviewer))

    def test_검수자도_공백을_지워_저장한다(self):
        assert parse(broken(reviewed_by=" nnhhlee ")).reviewed_by == "nnhhlee"

    def test_doc_key_가_겹치면_실패한다(self):
        meta = parse(ROW)
        with pytest.raises(ValueError, match="겹친다"):
            check_unique_doc_keys([meta, meta], file="t.yaml")


def test_월령_끝은_v1_범위_상한이다():
    """72 를 여기서 따로 정하지 않는다 — 나이 경계는 app/rules/age.py 한 곳에 있다."""
    assert DOC_MONTH_LIMIT == age_gates().v1_month_limit


# ── 공개용 출처 투영 (#510 Part C) ────────────────────────────────────────────

NHS_ROW = {
    "source_title": "(테스트) 가짜 NHS 문서 제목",
    "source_org": "NHS (테스트)",
    "source_url": "https://www.nhs.uk/test/fake-page/",
    "license_basis": "ogl_3",
}
DFE_ROW = {
    "source_title": "(테스트) 가짜 DfE 문서 제목",
    "source_org": "Department for Education (테스트)",
    "source_url": "https://www.gov.uk/government/publications/fake",
    "license_basis": "ogl_3",
}
NZ_ROW = {
    "source_title": "(테스트) 가짜 뉴질랜드 지침",
    "source_org": "Ministry of Health (New Zealand)",
    "source_url": "https://www.health.govt.nz/fake.pdf",
    "license_basis": "cc_by",
}


def public(**change):
    return public_doc_source(parse(broken(**change)))


class TestPublicDocSource:
    def test_표시_상수는_약관_문구와_한_글자도_다르지_않다(self):
        """OGL 은 이 문장을 그대로 써야 하는 조건이다 — 공백 · 마침표까지 같아야 한다."""
        assert OGL_STATEMENT == (
            "Contains public sector information licensed under the Open Government Licence v3.0."
        )
        assert (
            OGL_URL == "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
        )
        assert CC_BY_URL == "https://creativecommons.org/licenses/by/4.0/"

    def test_CC_BY_는_원래_기관과_제목을_두고_변경_표시와_라이선스_주소를_붙인다(self):
        got = public(**NZ_ROW)

        assert got.source_org == "Ministry of Health (New Zealand)"
        assert got.source_title.startswith("(테스트) 가짜 뉴질랜드 지침")
        assert "번역·재구성함" in got.source_title
        assert "CC BY 4.0" in got.source_title
        assert CC_BY_URL in got.source_title
        # 원문 링크는 source_url 칸으로 나간다. 제목 뒤에는 라이선스 주소만 붙는다
        assert got.source_url == "https://www.health.govt.nz/fake.pdf"

    def test_OGL_문서는_원래_기관과_제목_뒤에_필수_문장과_주소를_붙인다(self):
        got = public(**DFE_ROW)

        assert got.source_org == "Department for Education (테스트)"
        assert got.source_title.startswith("(테스트) 가짜 DfE 문서 제목")
        assert f"{OGL_STATEMENT} ({OGL_URL})" in got.source_title
        assert got.source_url == "https://www.gov.uk/government/publications/fake"

    def test_NHS_문서는_기관_제목_주소를_OGL_안내로_바꾼다(self):
        got = public(**NHS_ROW)

        assert got == PublicDocSource(
            source_org=NHS_PUBLIC_ORG,
            source_title=f"{OGL_STATEMENT} ({OGL_URL})",
            source_url=OGL_URL,
        )
        assert NHS_PUBLIC_ORG == "영국 정부 공공 정보"

    def test_NHS_원문_정보는_DocMeta_에_그대로_남는다(self):
        """공개 응답만 가린다 — 행을 되짚어 검수할 수 있어야 한다."""
        meta = parse(broken(**NHS_ROW))

        assert meta.source_org == "NHS (테스트)"
        assert meta.source_title == "(테스트) 가짜 NHS 문서 제목"
        assert meta.source_url == "https://www.nhs.uk/test/fake-page/"
        assert meta.source_locator

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.nhs.uk/x",
            "https://nhs.uk/x",
            "https://WWW.NHS.UK/x",
            "https://www.nhs.uk:443/x",
            "https://start-for-life.nhs.uk/x",
        ],
    )
    def test_nhs_uk_와_하위_도메인은_모두_NHS_로_본다(self, url):
        got = public(**{**DFE_ROW, "source_url": url})

        assert got.source_org == NHS_PUBLIC_ORG
        assert got.source_url == OGL_URL

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.gov.uk/nhs.uk",
            "https://notnhs.uk/x",
            "https://nhs.uk.example.com/x",
            "https://example.com/?u=www.nhs.uk",
        ],
    )
    def test_nhs_uk_가_아닌_비슷한_주소는_NHS_로_보지_않는다(self, url):
        got = public(**{**DFE_ROW, "source_url": url})

        assert got.source_org == "Department for Education (테스트)"
        assert got.source_url == url

    def test_주소가_없어도_기관이나_제목에_NHS_가_있으면_가린다(self):
        """NHS 이름이 새는 쪽이 약관 위반이다 — 주소를 못 믿으면 가리는 쪽으로 닫는다."""
        for change in (
            {"source_url": None, "source_org": "NHS England"},
            {"source_url": None, "source_title": "A guide from the nhs"},
        ):
            got = public(**{**DFE_ROW, **change})

            assert got.source_org == NHS_PUBLIC_ORG
            assert got.source_title == f"{OGL_STATEMENT} ({OGL_URL})"

    def test_NHS_가_아닌_OGL_행은_NHS_로_오인하지_않는다(self):
        got = public(**{**DFE_ROW, "source_org": "Department for Education", "source_url": None})

        assert got.source_org == "Department for Education"

    def test_NHS_가_아닌_다른_이름의_일부는_NHS_로_보지_않는다(self):
        got = public(**{**DFE_ROW, "source_org": "Inhsley Foundation"})

        assert got.source_org == "Inhsley Foundation"

    @pytest.mark.parametrize(
        "basis",
        ["public_law", "kogl_1", "kogl_2", "kogl_3", "kogl_4", "fact_rewrite", "permission"],
    )
    def test_그_밖의_기존_라이선스는_현재_공개_값을_그대로_둔다(self, basis):
        got = public(license_basis=basis, source_url="https://example.org/doc")

        assert got == PublicDocSource(
            source_org="보건복지부",
            source_title="제4차 어린이집 표준보육과정",
            source_url="https://example.org/doc",
        )

    @pytest.mark.parametrize("basis", ["pdl_1", "cc0"])
    def test_행이_없는_라이선스는_출처_문구를_지어내지_않는다(self, basis):
        """일본 자료 등을 들이는 PR 이 원문 정책과 같이 표시 문구를 정한다 (#510 Part F)."""
        got = public(license_basis=basis)

        assert got.source_title == "제4차 어린이집 표준보육과정"
        assert "Open Government" not in got.source_title
        assert "CC BY" not in got.source_title
