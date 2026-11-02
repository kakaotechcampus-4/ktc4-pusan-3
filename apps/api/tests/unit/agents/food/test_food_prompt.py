"""Food Agent system prompt."""

from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agents.food.context import FoodContext
from app.agents.food.prompt import build_system_prompt
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store import FoodDocRow, in_memory_ports

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


def test_영양소_분석_프롬프트는_모델에게_안_여는_tool_을_말하지_않는다() -> None:
    # 스텁 tool 은 열지 않는 것으로 막는다 — "부르지 않아도 된다" 는 안내로 우회하지 않는다
    # (#357 리뷰 4-①)
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    for name in ("search_food_memory", "analyze_meal_records", "check_repeated_menus"):
        assert name not in text


def test_영양소_분석_프롬프트는_문장의_주어를_먹은_식사로_정한다() -> None:
    # 문장 규칙(nutrient_wording)이 거절하는 것을 모델이 처음부터 안 쓰게 한다 (#357 리뷰 1)
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "findings 의 주어는 아이가 아니라 먹은 식사" in text


def test_영양소_분석_프롬프트는_코드가_준_식품군_안내_밖의_음식만_막는다() -> None:
    # 코드 문장 틀은 "— 고기·생선·콩 반찬 늘리기" 처럼 식품군 안내를 붙인다. 모델에게는 먹일 음식을
    # 통째로 금지한다고 말하면서 코드는 안내를 내면 같은 화면에 기준이 둘이 된다 (최종 리뷰)
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "food_group_actions 에 없는 음식을 제안하지 않는다" in text
    assert "먹일 음식을 제안하지 않는다" not in text


def test_수치를_만들지도_쓰지도_않는다고_말한다() -> None:
    # 모델에게 가는 tool 결과에 영양 수치가 없다(lookup_nutrition 의 per_100g 삭제) — "tool 결과에
    # 있는 수치는 내부적으로 쓴다" 는 문장은 더 이상 맞지 않는다
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "tool 결과에 있는 것만" not in text


def test_구간_표현은_식품을_말하는_적은_편_많은_편이다() -> None:
    # "칼슘이 낮은 편" 은 검사 수치처럼 읽힌다 — 코드 문장 틀 · 거절 메시지와 같은 표현을 쓴다
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "구간(적은 편 · 적당 · 많은 편)" in text
    assert "낮은 편" not in text


def test_식단_추천에는_기록_조회_안내가_없다() -> None:
    text = build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION)

    assert "compare_diet_balance" not in text


def test_예시_구획은_문서_행의_id_제목_본문과_출처_기관을_싣는다() -> None:
    row = FoodDocRow(
        id=uuid4(),
        row_type="weaning_stage",
        title="질감 올리기",
        body="걸쭉한 정도에서 시작한다.",
        min_month=6,
        max_month=8,
        texture=None,
        food_groups=frozenset(),
        allergen_codes=frozenset(),
        source_org="가짜 기관",
        written_at=datetime(2026, 10, 14, tzinfo=KST),
    )

    text = build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION, (row,))

    assert "질감 올리기" in text
    assert "걸쭉한 정도에서 시작한다." in text
    assert "가짜 기관" in text  # 출처 표시가 이용 조건인 행이 있다 — 모델이 인용할 때 같이 본다
    assert str(row.id) in text  # 인용하려면 id 가 보여야 한다


def test_식단_추천_프롬프트는_재료를_모델에게_적게_하지_않는다() -> None:
    # 메뉴 이름 · 재료는 코드가 카탈로그에서 채운다 — 모델이 재료를 빠뜨려도 필터가 새지 않는다
    text = build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION)

    assert "[재료]" not in text
    assert "재료를 빠짐없이" not in text


def test_식단_추천_프롬프트는_보여_준_후보에서_menu_key_로만_고르게_한다() -> None:
    text = build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION)

    assert "menu_key" in text
    assert "보여 준 후보" in text


def test_영양소_분석_프롬프트에는_후보_고르기_안내가_없다() -> None:
    text = build_system_prompt(context(), FoodTaskType.NUTRIENT_ANALYSIS)

    assert "menu_key" not in text


LINE = (
    "- 보호자가 말한 음식이 후보 목록에 없으면 그 음식을 reason 에 쓰지 않는다. "
    "후보 안에서 가까운 것을 고른다."
)


def test_식단_추천_프롬프트는_후보_밖_음식을_reason_에_쓰지_않게_한다() -> None:
    assert LINE in build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION)
    assert LINE not in build_system_prompt(context(), FoodTaskType.DAYCARE_MEAL)


ALLERGY_LINE = (
    "- 보호자가 알레르기나 피할 음식을 말했어도 reason 에는 그 이름이나 "
    "'알레르기를 고려해' 같은 말을 쓰지 않는다. 고른 메뉴가 왜 지금 맞는지만 쓴다."
)


def test_식단_추천_프롬프트는_요청에_쓴_알레르기를_reason_에_되풀이하지_않게_한다() -> None:
    assert ALLERGY_LINE in build_system_prompt(context(), FoodTaskType.MEAL_RECOMMENDATION)
    assert ALLERGY_LINE not in build_system_prompt(context(), FoodTaskType.DAYCARE_MEAL)


def test_코드가_답하는_섭취_확인은_프롬프트를_만들지_않는다() -> None:
    with pytest.raises(ValueError, match="프롬프트"):
        build_system_prompt(context(), FoodTaskType.FOOD_CHECK)
