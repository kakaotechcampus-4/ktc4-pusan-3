"""activity_doc 시드 내용 — 예시가 우리 출력 검증을 통과하는 모양인가 (D10 · RAG_plan Activity 절).

예시가 출력 검증에 걸리는 모양이면 모델에게 거절될 문장을 가르치는 셈이다. 그래서 행마다
출력 후보가 받는 검사를 그대로 건다 — 위험 용어 · 보호자 역할 · 평가 표현.
원문과의 문장 겹침(8어절)은 원문이 저장소 밖이라 테스트로 못 본다. 검수자가 본다.
"""

import pytest

from app.agents.activity.doc_seed import activity_doc_seed, parse_activity_doc
from app.agents.activity.review import TOGETHER_ONLY_BELOW_MONTH
from app.agents.activity.schemas.common import CaregiverRole
from app.agents.activity.store.inmemory import InMemoryActivityDocs
from app.agents.common.reference import DOC_MONTH_LIMIT, hazard_terms
from app.rules.evaluative import find_evaluative
from app.rules.term_match import match_terms

SEED = activity_doc_seed()
IDS = [entry.meta.doc_key for entry in SEED]

# 모든 월령에서 이 수 이상의 행이 있어야 한다. 80행을 채우면 DOC_LIMIT(5)로 올린다
MIN_ROWS_PER_MONTH = 1


def hazard_hits(entry) -> list[tuple[str, str]]:
    """그 행의 가장 어린 월령에서 걸리는 (축, 판정). 축 판정은 나이가 많을수록 약해진다."""
    dictionary = hazard_terms()
    hits = []
    # 문장 · 재료를 따로 대조한다. 이어 붙이면 없는 위험이 생긴다 (3-5)
    for text in (entry.meta.title, entry.meta.body, *entry.materials):
        for key in match_terms(text, dictionary.terms):
            axis = dictionary.axis_of(key)
            if (level := axis.level_at(entry.meta.min_month)) is not None:
                hits.append((axis.name, level))
    return hits


@pytest.mark.parametrize("entry", SEED, ids=IDS)
def test_가장_어린_월령에서_위험_용어에_걸리지_않는다(entry):
    """경고도 안 된다. 예시는 그대로 통과하는 모양만 보여 준다."""
    assert hazard_hits(entry) == []


@pytest.mark.parametrize("entry", SEED, ids=IDS)
def test_18개월_미만은_보호자가_같이_한다(entry):
    if entry.meta.min_month < TOGETHER_ONLY_BELOW_MONTH:
        assert entry.caregiver_role is CaregiverRole.TOGETHER


@pytest.mark.parametrize("entry", SEED, ids=IDS)
def test_평가_표현이_없다(entry):
    assert find_evaluative(entry.meta.title) == ()
    assert find_evaluative(entry.meta.body) == ()


@pytest.mark.parametrize("entry", SEED, ids=IDS)
def test_출처는_고시_본문이다(entry):
    """지금 판은 고시 본문만 쓴다. 다른 출처를 넣으려면 RAG_plan Activity 소스 표부터 고친다."""
    assert entry.meta.license_basis == "public_law"
    assert "고시" in entry.meta.source_title


def test_모든_월령이_행으로_덮인다():
    for months in range(DOC_MONTH_LIMIT):
        covering = [e for e in SEED if e.meta.covers(months)]
        assert len(covering) >= MIN_ROWS_PER_MONTH, f"{months}개월을 덮는 행이 부족하다"


class TestInMemoryDocs:
    @staticmethod
    async def keys(docs: InMemoryActivityDocs, months: int) -> list[str]:
        return [row.doc_key for row in await docs.search(months=months, query="", limit=99)]

    async def test_월령은_max_month_미만까지다(self):
        docs = InMemoryActivityDocs([entry.to_row() for entry in SEED])
        assert "activity.play.m18_23.cushion_crawl" in await self.keys(docs, 23)
        assert "activity.play.m18_23.cushion_crawl" not in await self.keys(docs, 24)

    async def test_from_seed_는_검수를_마친_행만_싣는다(self):
        approved = {e.meta.doc_key for e in SEED if e.meta.status == "approved"}
        docs = InMemoryActivityDocs.from_seed()
        served = {key for months in range(DOC_MONTH_LIMIT) for key in await self.keys(docs, months)}
        assert served == approved

    def test_id_는_doc_key_로_늘_같다(self):
        (first, *_) = SEED
        assert first.to_row().id == first.to_row().id


class TestParse:
    def test_rows_가_없으면_실패한다(self):
        with pytest.raises(ValueError, match="rows"):
            parse_activity_doc({"rows": []})

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("area", "math", "area"),
            ("setting", "anywhere", "setting"),
            ("involves_food", "no", "involves_food"),
            ("materials", "구슬", "materials"),
        ],
    )
    def test_도메인_칸이_깨지면_실패한다(self, field, value, message):
        raw = {**_raw_row(), field: value}
        with pytest.raises(ValueError, match=message):
            parse_activity_doc({"rows": [raw]})


def _raw_row() -> dict:
    meta = SEED[0].meta
    return {
        "doc_key": meta.doc_key,
        "row_type": meta.row_type,
        "title": meta.title,
        "body": meta.body,
        "min_month": meta.min_month,
        "max_month": meta.max_month,
        "tags": list(meta.tags),
        "source_title": meta.source_title,
        "source_org": meta.source_org,
        "source_year": meta.source_year,
        "source_locator": meta.source_locator,
        "license_basis": meta.license_basis,
        "status": meta.status,
        "authored_by": meta.authored_by,
        "version": meta.version,
        "setting": "indoor",
        "materials": [],
        "caregiver_role": "together",
        "physical_intensity": "low",
        "involves_food": False,
        "area": "physical_health",
    }
