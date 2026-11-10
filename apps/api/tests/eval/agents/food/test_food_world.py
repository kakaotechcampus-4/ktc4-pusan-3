"""Food eval 세계가 의도한 영양 판정을 타는지 — 모델을 부르지 않는다(키 없이 돈다).

영양소 분석은 모델 0회라 라이브 eval(`test_food_run.py`)이 다루지 않는다(N-17). 그래서 영양소 분석
문장과 식단 추천 가중은 여기서 세계(`world.py`)를 직접 돌려 본다. 라이브를 돌리기 전에 세계가 판정을
제대로 타는지 먼저 확인하는 자리이기도 하다 — C48-veg 는 급식 메뉴가 카탈로그에 빠지면 평일에
채소 줄이 사라지는데, 라이브는 실행한 날의 요일 하나만 보므로 일곱 요일을 여기서 다 본다.

기준은 `docs/agents/food/test_expected_food.md` 의 N1~N6.
"""

from datetime import datetime, timedelta

import pytest

from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run
from app.agents.food.appearance import explainable_groups, sparse_groups
from app.agents.food.context import build_gate
from app.agents.food.meal_pool import (
    AVOID_FACTOR,
    DEFICIT_BOOST,
    FEW_RECORDS_BOOST,
    TODAY_OVERLAP_FACTOR,
    build_pool,
)
from app.agents.food.nutrient_wording import APPEARANCE_GROUPS, wording_problem
from app.agents.food.readouts import READOUTS
from tests.eval.agents.food.test_food_run import _DIAGNOSIS, _UNIT
from tests.eval.agents.food.world import KST, build_world

MONDAY = datetime(2026, 11, 9, 9, 0, tzinfo=KST)
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
VEG = READOUTS.render("nutrient.sparse", group="채소가").body
FRUIT = READOUTS.render("nutrient.sparse", group="과일이").body
AMOUNT_UNKNOWN = READOUTS.get("nutrient.daycare_amount_unknown").template


def _at(offset: int) -> datetime:
    return MONDAY + timedelta(days=offset)


def _appearance(days: int, counts: dict[str, int]) -> str:
    return "\n".join(
        READOUTS.render(
            "nutrient.appearance", days=days, group=label, count=counts.get(group, 0)
        ).body
        for group, label in APPEARANCE_GROUPS
    )


async def _analyze(name: str, now: datetime):
    world = build_world(name, now=now, run_id=f"world-{name}")
    task = DomainTask(
        run_id=world.context.run_id,
        agent="food",
        task_type="nutrient_analysis",
        request_texts=("영양 괜찮아?",),
    )
    result = await run(task, world.context)  # 모델을 부르지 않으니 client 가 필요 없다
    assert (result.status, result.model_calls) == ("completed", 0)
    return world, result


async def _pool_weight(name: str, now: datetime, menu: str) -> float:
    world = build_world(name, now=now, run_id=f"world-pool-{name}")
    world.context.state.gate = await build_gate(world.context)
    pool = await build_pool(world.context)
    return next(item.weight for item in pool if item.row.menu_key == menu)


# ── N1 · C48-wk — 집 기록 0, 이번 주 급식표만 ─────────────────────────────────
async def test_N1_이번_주_급식표만_있어도_출현과_다섯_줄이_순서대로_나온다() -> None:
    now = _at(2)  # 수요일

    world, result = await _analyze("C48-wk", now)

    assert [readout.body for readout in result.readouts] == [
        _appearance(5, {"grains": 5, "meat_fish_egg_legumes": 5, "vegetables": 1, "fruits": 3}),
        VEG,
        READOUTS.render("nutrient.few_records", recorded=5).body,
        READOUTS.render("nutrient.daycare_upcoming", days=3).body,
        AMOUNT_UNKNOWN,
    ]
    verdict = world.context.state.nutrient_verdict
    assert verdict is not None
    assert (verdict.appearance_records, verdict.recorded_days) == (5, 5)


async def test_N1_구간_창은_그대로라_2행뿐이고_구간은_막힌다() -> None:
    world, _ = await _analyze("C48-wk", _at(2))

    verdict = world.context.state.nutrient_verdict
    assert verdict is not None
    assert (verdict.records, verdict.insufficient, verdict.bands) == (2, True, {})


# ── N3 · C48-few — 급식 없이 집 기록 4행 · 2일 ─────────────────────────────────
async def test_N3_적은_기록은_출현_짚기_적은_기록_세_줄이고_급식_줄이_없다() -> None:
    world, result = await _analyze("C48-few", _at(2))

    assert [readout.body for readout in result.readouts] == [
        _appearance(2, {"grains": 2, "meat_fish_egg_legumes": 1, "fruits": 1}),
        VEG,  # 과일은 1일 — 기록된 2일의 절반이라 적게 나온 게 아니다
        READOUTS.render("nutrient.few_records", recorded=4).body,
    ]
    verdict = world.context.state.nutrient_verdict
    assert verdict is not None
    assert (verdict.records, verdict.insufficient) == (4, True)


# ── N5 · C48-veg — 일곱 요일 모두 같은 결론 ─────────────────────────────────────
@pytest.mark.parametrize("offset", range(7), ids=WEEKDAYS)
async def test_N5_평일과_주말_모두_채소_과일을_짚고_급식_범위를_밝힌다(offset: int) -> None:
    world, result = await _analyze("C48-veg", _at(offset))

    bodies = [readout.body for readout in result.readouts]
    upcoming = 2 if offset < 5 else 1  # 오늘 + 모레 급식은 이번 주 안일 때만(토 · 일은 오늘뿐)
    assert bodies[1:] == [
        VEG,
        FRUIT,  # 10행 이상이라 적은 기록 줄은 없어도 짚는다
        READOUTS.render("nutrient.daycare_upcoming", days=upcoming).body,
        AMOUNT_UNKNOWN,
    ]
    verdict = world.context.state.nutrient_verdict
    assert verdict is not None
    assert verdict.few_records is False
    assert (verdict.records, verdict.insufficient) == (13, True)  # 구간 창은 요일과 무관하다


# ── N6 · C48-veg-unknown — 가중은 걸려도 문장은 없다 ───────────────────────────
@pytest.mark.parametrize("offset", range(7), ids=WEEKDAYS)
async def test_N6_모르는_날을_더하면_절반_이상이라_짚지_않는다(offset: int) -> None:
    world, result = await _analyze("C48-veg-unknown", _at(offset))

    bodies = [readout.body for readout in result.readouts]
    assert VEG not in bodies and FRUIT not in bodies
    verdict = world.context.state.nutrient_verdict
    assert verdict is not None
    assert "vegetables" in sparse_groups(verdict)  # 확인한 날만 보면 적어서 가중은 걸린다
    assert explainable_groups(verdict) == frozenset()


# ── 식단 추천 가중 ──────────────────────────────────────────────────────────
async def test_적은_기록이면_채소_메뉴는_2배_가중이다() -> None:
    assert await _pool_weight("C48-wk", _at(2), "시금치나물") == pytest.approx(FEW_RECORDS_BOOST)
    assert await _pool_weight("C48-few", _at(2), "시금치나물") == pytest.approx(FEW_RECORDS_BOOST)


async def test_적은_기록_가중은_기피를_상쇄하지_못한다() -> None:
    # C48-few 는 브로콜리를 안 먹는 아이다 — 2.0 * 0.3
    weight = await _pool_weight("C48-few", _at(2), "브로콜리볶음")

    assert weight == pytest.approx(FEW_RECORDS_BOOST * AVOID_FACTOR)
    assert weight < 1


@pytest.mark.parametrize("offset", range(7), ids=WEEKDAYS)
async def test_10행_이상이면_4배이고_오늘_급식에_채소가_있어_겹침이_곱해진다(offset: int) -> None:
    weight = await _pool_weight("C48-veg", _at(offset), "시금치나물")

    assert weight == pytest.approx(DEFICIT_BOOST * TODAY_OVERLAP_FACTOR)


# ── 가드레일 — 새 줄도 공통 실패 조건과 문장 규칙을 지킨다 ──────────────────────
@pytest.mark.parametrize("name", ["C48-wk", "C48-few", "C48-veg"])
async def test_영양소_분석_문장에_단위_문자와_진단어와_문장_규칙_위반이_없다(name: str) -> None:
    # 공통1(% · g · mg · kcal) · 공통5(결핍 · 부족증 · 비만) · 먹은 식사가 주어인 문장 규칙
    _, result = await _analyze(name, _at(2))

    text = "\n".join(readout.body for readout in result.readouts)
    assert not _UNIT.search(text)
    assert not any(word in text for word in _DIAGNOSIS)
    for readout in result.readouts:
        for line in readout.body.splitlines():
            assert wording_problem(line) is None, line
