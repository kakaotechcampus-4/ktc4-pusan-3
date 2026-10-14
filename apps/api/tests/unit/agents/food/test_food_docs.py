"""`search_food_doc` — 월령 · row_type · 알레르기로 거른 문서 행 (#396 T3).

시드(`food_doc.yaml`)를 그대로 읽는 검사와, 일부러 만든 가짜 행으로 거르기를 보는 검사를 같이 둔다.
시드 행은 아직 전부 draft 라서 `include_draft=True` 로만 읽는다.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.food.doc_seed import ROW_TYPES, WEANING_TYPES, food_doc_seed
from app.agents.food.store import FoodDocRow, InMemoryFoodDocs, SafetyEntry
from app.agents.food.tools.docs import DOC_LIMIT, search_food_doc

NOW = datetime(2026, 10, 14, tzinfo=UTC)
WEANING = tuple(sorted(WEANING_TYPES))
EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]
MILK = [SafetyEntry(kind="allergy", label="우유", status="active")]


def doc(
    title: str,
    *,
    body: str = "가짜 문장이다.",
    row_type: str = "weaning_stage",
    months: tuple[int, int] = (4, 12),
    codes: frozenset[int] = frozenset(),
    org: str = "가짜 기관",
) -> FoodDocRow:
    return FoodDocRow(
        id=uuid4(),
        row_type=row_type,
        title=title,
        body=body,
        min_month=months[0],
        max_month=months[1],
        texture=None,
        food_groups=frozenset(),
        allergen_codes=codes,
        source_org=org,
        written_at=NOW,
    )


def seed_docs() -> InMemoryFoodDocs:
    return InMemoryFoodDocs.from_seed(include_draft=True)


# ── 시드 ──────────────────────────────────────────────────────────────
async def test_기본_시드는_검수를_마친_행만_읽는다() -> None:
    # 시드 행은 아직 전부 draft 다 — 서비스는 approved 만 쓴다
    assert {e.meta.status for e in food_doc_seed()} == {"draft"}

    approved_only = await InMemoryFoodDocs.from_seed().search(months=6, row_types=WEANING, limit=99)
    with_draft = await seed_docs().search(months=6, row_types=WEANING, limit=99)

    assert approved_only == []
    assert with_draft


async def test_시드_행에_제목_월령_출처_기관_식품군이_실린다() -> None:
    rows = await seed_docs().search(months=6, row_types=WEANING, limit=99)

    assert rows
    assert all(r.title and r.source_org for r in rows)
    assert all(r.min_month <= 6 < r.max_month for r in rows)
    assert any(r.food_groups for r in rows)


async def test_시드_행의_알레르기_코드는_본문을_훑은_코드와_합친_값이다() -> None:
    # 달걀이 나오는 행 하나는 YAML 코드와 본문 훑기가 같다 — 합친 값에 1 이 있다
    rows = await seed_docs().search(months=8, row_types=WEANING, limit=99)

    assert any(1 in r.allergen_codes for r in rows)


# ── 월령 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("months", range(4, 12))
async def test_이유기_단계_행은_월령에_맞는_것만_나온다(months: int) -> None:
    # 기준은 시드의 월령(이상 · 미만)이다 — 4–5 · 6–7 · 8–11개월 단계가 infant_weaning 안에서 갈린다
    expected = {
        e.meta.title
        for e in food_doc_seed()
        if e.meta.row_type == "weaning_stage" and e.meta.min_month <= months < e.meta.max_month
    }

    rows = await seed_docs().search(months=months, row_types=("weaning_stage",), limit=99)

    assert expected
    assert {r.title for r in rows} == expected


async def test_단계가_다른_행은_섞이지_않는다() -> None:
    at_5 = await seed_docs().search(months=5, row_types=("weaning_stage",), limit=99)
    at_9 = await seed_docs().search(months=9, row_types=("weaning_stage",), limit=99)

    assert not any(r.min_month > 5 for r in at_5)  # 6개월부터인 행이 5개월에 나오지 않는다
    assert not any(r.max_month <= 9 for r in at_9)  # 8개월 전에 끝난 행이 9개월에 나오지 않는다


async def test_월령_경계는_이상_미만이다() -> None:
    docs = InMemoryFoodDocs([doc("끝은 미만", months=(4, 6)), doc("시작은 이상", months=(6, 8))])

    at_5 = await search_food_doc(docs, months=5, row_types=("weaning_stage",), safety=())
    at_6 = await search_food_doc(docs, months=6, row_types=("weaning_stage",), safety=())

    assert [r.title for r in at_5] == ["끝은 미만"]
    assert [r.title for r in at_6] == ["시작은 이상"]


async def test_row_type_이_다른_행은_나오지_않는다() -> None:
    docs = InMemoryFoodDocs(
        [doc("단계", row_type="weaning_stage"), doc("식사 태도", row_type="feeding_tip")]
    )

    rows = await search_food_doc(docs, months=8, row_types=("weaning_stage",), safety=())

    assert [r.title for r in rows] == ["단계"]


@pytest.mark.parametrize("row_types", [(), ("없는종류",)])
async def test_row_type_이_비었거나_모르는_값이면_거절한다(row_types: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="row_type"):
        await search_food_doc(InMemoryFoodDocs(), months=8, row_types=row_types, safety=())


def test_조회하는_row_type_은_시드가_쓰는_종류_안이다() -> None:
    assert set(WEANING) <= ROW_TYPES


# ── 알레르기 ──────────────────────────────────────────────────────────
async def test_달걀_알레르기_아이에게_달걀이_든_행이_안_나온다() -> None:
    # 후보가 시드 전체일 때 달걀 행이 있다 — 아래 검사가 빈 검사가 아니다
    wide = await seed_docs().search(months=8, row_types=WEANING, limit=99)
    assert any(1 in r.allergen_codes for r in wide)

    rows = await search_food_doc(seed_docs(), months=8, row_types=WEANING, safety=EGG)

    assert len(rows) == DOC_LIMIT
    assert all(1 not in r.allergen_codes for r in rows)
    assert all("달걀" not in r.body and "계란" not in r.body for r in rows)


async def test_저장된_코드가_비어_있어도_본문의_알레르기_식품이면_뺀다() -> None:
    # 어댑터가 코드를 빠뜨려도 본문을 사전으로 훑어 막는다
    leaking = doc("달걀 삶기", body="삶은 달걀을 잘게 으깨어 준다.", codes=frozenset())
    safe = doc("채소 으깨기", body="익힌 감자를 포크로 으깬다.")

    rows = await search_food_doc(
        InMemoryFoodDocs([leaking, safe]), months=8, row_types=("weaning_stage",), safety=EGG
    )

    assert [r.title for r in rows] == ["채소 으깨기"]


async def test_행의_코드가_알레르기와_겹치면_본문에_이름이_없어도_뺀다() -> None:
    row = doc("단백질 시작", body="부드럽게 익힌 단백질을 준다.", codes=frozenset({2}))

    rows = await search_food_doc(
        InMemoryFoodDocs([row]), months=8, row_types=("weaning_stage",), safety=MILK
    )

    assert rows == ()


async def test_알레르기가_없는_아이에게는_거르지_않는다() -> None:
    row = doc("우유 순서", codes=frozenset({2}))

    rows = await search_food_doc(
        InMemoryFoodDocs([row]), months=8, row_types=("weaning_stage",), safety=()
    )

    assert rows == (row,)


async def test_철회한_알레르기는_거르지_않는다() -> None:
    retracted = [SafetyEntry(kind="allergy", label="우유", status="retracted")]
    row = doc("우유 순서", codes=frozenset({2}))

    rows = await search_food_doc(
        InMemoryFoodDocs([row]), months=8, row_types=("weaning_stage",), safety=retracted
    )

    assert rows == (row,)


async def test_알레르기로_빠진_자리는_다음_행이_채운다() -> None:
    # 포트가 DOC_LIMIT 만 돌려주면 걸러진 만큼 비어 버린다 — 거른 뒤에 자른다
    blocked = [doc(f"우유 {i}", codes=frozenset({2})) for i in range(DOC_LIMIT)]
    safe = [doc(f"안전 {i}") for i in range(DOC_LIMIT)]

    rows = await search_food_doc(
        InMemoryFoodDocs([*blocked, *safe]), months=8, row_types=("weaning_stage",), safety=MILK
    )

    assert [r.title for r in rows] == [r.title for r in safe]


# ── 출처 기관 · 개수 ──────────────────────────────────────────────────
async def test_결과_행에_출처_기관이_있다() -> None:
    rows = await search_food_doc(seed_docs(), months=6, row_types=WEANING, safety=())

    assert rows
    assert all(r.source_org for r in rows)
    wide = await seed_docs().search(months=6, row_types=WEANING, limit=99)
    # 뉴질랜드 보건부 행은 CC BY 4.0 이라 출처 표시가 조건이다
    assert any(r.source_org == "Ministry of Health (New Zealand)" for r in wide)


async def test_최대_세_행이다() -> None:
    docs = InMemoryFoodDocs([doc(f"행 {i}") for i in range(10)])

    rows = await search_food_doc(docs, months=8, row_types=("weaning_stage",), safety=())

    assert DOC_LIMIT == 3
    assert len(rows) == 3


async def test_0행이면_빈_튜플이다() -> None:
    rows = await search_food_doc(
        InMemoryFoodDocs(), months=8, row_types=("weaning_stage",), safety=()
    )

    assert rows == ()
