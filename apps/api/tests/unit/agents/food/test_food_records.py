"""`search_food_memory` — 아이의 식사 기록과 음식 선호 · 기피를 근거 순서대로 돌려준다.

아이 이름 · 메뉴 · 기록은 전부 가짜다. 포트는 InMemory 다.
"""

import pytest

from app.agents.common.evidence import TOP_N
from app.agents.food.result import ErrorCode
from app.agents.food.schemas.common import MealPeriod
from app.agents.food.schemas.records import SearchFoodMemoryArgs
from app.agents.food.store import InMemoryFoodMemory
from app.agents.food.tools.records import prefetch_food_memory, search_food_memory
from tests.unit.agents.food.support import CHILD, TODAY, affinity, context, observation


def memory(*, affinities=(), observations=()) -> InMemoryFoodMemory:
    return InMemoryFoodMemory({CHILD: list(affinities)}, {CHILD: list(observations)})


# ── search_food_memory ────────────────────────────────────────────────
async def test_선호와_기피가_kind_id_polarity_로_나온다() -> None:
    liked = affinity("딸기", polarity=1)
    disliked = affinity("브로콜리", polarity=-1)
    ctx = context(memory=memory(affinities=[liked, disliked]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    assert result.success
    records = {r["id"]: r for r in result.data["records"]}
    assert records[str(liked.id)]["kind"] == "profile_affinity"
    assert records[str(liked.id)]["polarity"] == 1
    assert records[str(disliked.id)]["polarity"] == -1  # 기피도 근거로 남는다
    assert records[str(disliked.id)]["label"] == "브로콜리"


async def test_확정된_선호가_최근_관찰보다_앞에_온다() -> None:
    seen_meal = observation("계란말이", days_ago=0)
    liked = affinity("딸기", state="confirmed")
    ctx = context(memory=memory(affinities=[liked], observations=[seen_meal]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    kinds = [r["kind"] for r in result.data["records"]]
    assert kinds == ["profile_affinity", "observation_food"]


async def test_마지막_관찰이_6개월_넘은_선호는_낡았다고_표시한다() -> None:
    old = affinity("바나나", days_ago=200)
    fresh = affinity("딸기", days_ago=3)
    ctx = context(memory=memory(affinities=[old, fresh]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    stale = {r["label"]: r["is_stale"] for r in result.data["records"]}
    assert stale == {"바나나": True, "딸기": False}


async def test_결과에_발화_원문과_정해진_칸_밖의_값을_싣지_않는다() -> None:
    ctx = context(
        memory=memory(affinities=[affinity("딸기")], observations=[observation("계란말이")])
    )

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    allowed = {"kind", "id", "tier", "label", "polarity", "observed_on", "is_stale"}
    assert all(set(r) == allowed for r in result.data["records"])
    assert "raw_text" not in str(result.data)


async def test_조회한_근거는_run_state_에_남는다() -> None:
    liked = affinity("딸기")
    meal = observation("계란말이")
    ctx = context(memory=memory(affinities=[liked], observations=[meal]))

    await search_food_memory(ctx, SearchFoodMemoryArgs())

    assert set(ctx.state.seen_evidence) == {liked.id, meal.id}
    assert ctx.state.seen_evidence[liked.id].ref.kind == "profile_affinity"
    assert ctx.state.seen_evidence[meal.id].ref.kind == "observation_food"


async def test_상위_열_개만_돌려주고_그것만_run_state_에_남는다() -> None:
    rows = [affinity(f"음식{i}", strength=0.5 + i / 100) for i in range(TOP_N + 2)]
    ctx = context(memory=memory(affinities=rows))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    returned = {r["id"] for r in result.data["records"]}
    assert len(returned) == TOP_N
    # 모델이 보지 못한 근거는 인용할 수 없다 — 표도 같은 열 개다
    assert {str(key) for key in ctx.state.seen_evidence} == returned


async def test_기록이_없으면_NO_RECORDS_다() -> None:
    result = await search_food_memory(context(memory=memory()), SearchFoodMemoryArgs())

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.NO_RECORDS
    assert not context(memory=memory()).state.seen_evidence


async def test_keywords_가_있으면_맞는_근거만_남긴다() -> None:
    strawberry = affinity("딸기")
    ctx = context(memory=memory(affinities=[strawberry, affinity("브로콜리", polarity=-1)]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs(keywords=["딸기"]))

    assert [r["label"] for r in result.data["records"]] == ["딸기"]
    assert set(ctx.state.seen_evidence) == {strawberry.id}


async def test_keywords_에_맞는_기록이_없으면_NO_RECORDS_이고_keywords_를_싣지_않는다() -> None:
    ctx = context(memory=memory(affinities=[affinity("딸기")]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs(keywords=["가짜키워드"]))

    assert not result.success
    assert result.error is not None
    assert result.error["code"] == ErrorCode.NO_RECORDS
    assert "가짜키워드" not in result.error["message"]  # message 는 로그로 흘러간다
    assert not ctx.state.seen_evidence


async def test_기간_라벨은_코드가_날짜로_푼다() -> None:
    yesterday = observation("어제음식", days_ago=1)
    today = observation("오늘음식", days_ago=0)
    old = observation("오래된음식", days_ago=5)
    ctx = context(memory=memory(observations=[yesterday, today, old]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs(period=MealPeriod.YESTERDAY))

    assert [r["label"] for r in result.data["records"]] == ["어제음식"]


async def test_기간을_비우면_최근_14일_관찰을_다_본다() -> None:
    ctx = context(
        memory=memory(
            observations=[observation("열나흘전", days_ago=14), observation("보름전", days_ago=15)]
        )
    )

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    assert [r["label"] for r in result.data["records"]] == ["열나흘전"]


async def test_같은_음식을_여러_날_먹은_관찰은_가장_최근_한_건만_싣는다() -> None:
    recent = observation("딸기", days_ago=1)
    ctx = context(memory=memory(observations=[observation("딸기", days_ago=3), recent]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs())

    records = result.data["records"]
    assert [r["id"] for r in records] == [str(recent.id)]


@pytest.mark.parametrize(
    ("period", "inside", "outside"),
    [
        (MealPeriod.TODAY, [0], [1]),
        (MealPeriod.LAST_3_DAYS, [0, 1, 2], [3]),
        (MealPeriod.LAST_7_DAYS, [0, 6], [7]),
    ],
)
async def test_기간_라벨의_범위는_오늘을_포함한다(
    period: MealPeriod, inside: list[int], outside: list[int]
) -> None:
    # 메뉴 이름을 정규화하면 숫자가 떨어지므로 날마다 다른 이름을 쓴다
    names = ["딸기", "사과", "바나나", "포도", "귤", "수박", "멜론", "참외"]
    rows = [observation(names[n], days_ago=n) for n in (*inside, *outside)]
    ctx = context(memory=memory(observations=rows))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs(period=period))

    assert {r["label"] for r in result.data["records"]} == {names[n] for n in inside}


async def test_이번_주는_월요일부터_오늘까지다() -> None:
    before = observation("지난주", days_ago=TODAY.weekday() + 1)
    first = observation("월요일", days_ago=TODAY.weekday())
    ctx = context(memory=memory(observations=[before, first]))

    result = await search_food_memory(ctx, SearchFoodMemoryArgs(period=MealPeriod.THIS_WEEK))

    assert [r["label"] for r in result.data["records"]] == ["월요일"]


# ── prefetch_food_memory — 모델을 부르기 전 기본 검색 ─────────────────────
async def test_선조회는_도구와_같은_근거를_같은_순서로_고른다() -> None:
    liked = affinity("딸기", state="confirmed")
    meal = observation("계란말이", days_ago=0)
    old = observation("보름전", days_ago=15)
    ctx = context(memory=memory(affinities=[liked], observations=[meal, old]))
    by_tool = context(memory=memory(affinities=[liked], observations=[meal, old]))

    found = await prefetch_food_memory(ctx)
    result = await search_food_memory(by_tool, SearchFoodMemoryArgs())

    assert [str(item.ref.id) for item in found] == [r["id"] for r in result.data["records"]]
    assert [item.ref.id for item in found] == [liked.id, meal.id]


async def test_선조회한_근거는_seen_evidence_와_선조회_표에_남는다() -> None:
    liked = affinity("딸기")
    meal = observation("계란말이")
    ctx = context(memory=memory(affinities=[liked], observations=[meal]))

    await prefetch_food_memory(ctx)

    assert ctx.state.memory_prefetched
    assert ctx.state.prefetched_evidence_ids == {liked.id, meal.id}
    assert set(ctx.state.seen_evidence) == {liked.id, meal.id}


async def test_기록이_없어도_선조회를_했다고_남기고_빈_결과를_돌려준다() -> None:
    ctx = context(memory=memory())

    found = await prefetch_food_memory(ctx)

    assert found == ()
    assert ctx.state.memory_prefetched
    assert ctx.state.prefetched_evidence_ids == set()
    assert ctx.state.seen_evidence == {}


async def test_누적이_많아도_최근_14일_밖이고_관심도_없으면_선조회는_비어_있다() -> None:
    names = [f"음식{chr(0xAC00 + i)}" for i in range(22)]
    rows = [observation(name, days_ago=20 + i) for i, name in enumerate(names)]
    ctx = context(memory=memory(observations=rows))

    found = await prefetch_food_memory(ctx)

    assert found == ()


async def test_최근_관찰이_없어도_유효한_확정_관심은_선조회에_들어온다() -> None:
    liked = affinity("딸기", state="confirmed", days_ago=40)
    ctx = context(memory=memory(affinities=[liked]))

    found = await prefetch_food_memory(ctx)

    assert [item.ref.id for item in found] == [liked.id]


async def test_선조회는_모델이_도구를_불렀다는_횟수에_세지_않는다() -> None:
    ctx = context(memory=memory(affinities=[affinity("딸기")]))

    await prefetch_food_memory(ctx)
    assert ctx.state.memory_search_calls == 0

    await search_food_memory(ctx, SearchFoodMemoryArgs())
    assert ctx.state.memory_search_calls == 1
