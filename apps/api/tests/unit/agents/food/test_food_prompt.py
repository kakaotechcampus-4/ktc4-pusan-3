"""Food Agent system prompt."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.food.context import FoodContext
from app.agents.food.prompt import build_system_prompt
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import in_memory_ports

KST = ZoneInfo("Asia/Seoul")


def context() -> FoodContext:
    return FoodContext(
        child_id=UUID(int=1),
        run_id="run-1",
        now=datetime(2026, 10, 2, 9, 0, tzinfo=KST),
        timezone=KST,
        ports=in_memory_ports(),
    )


def test_아이_이름을_넣지_않는다() -> None:
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "민준" not in text


def test_예시_구획은_맨_뒤다() -> None:
    # 앞에 두면 프롬프트 캐시가 깨진다 (RAG_plan §3)
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert text.rindex("[예시]") > text.rindex("[지시]")


def test_수치를_쓰지_말라는_지시가_들어간다() -> None:
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "수치" in text


def test_영양소_분석에서는_기록_조회_tool_을_먼저_안_불러도_된다는_안내가_있다() -> None:
    # compare_diet_balance · lookup_nutrition 이 식사 기록을 직접 읽는다 — 모델이 아직 DB 연결
    # 전이라 NotImplementedError 인 search_food_memory · analyze_meal_records ·
    # check_repeated_menus 를 먼저 부르지 않도록 안내한다
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "compare_diet_balance" in text
    assert "lookup_nutrition" in text


def test_식단_추천에는_기록_조회_안내가_없다() -> None:
    text = build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION)

    assert "compare_diet_balance" not in text
