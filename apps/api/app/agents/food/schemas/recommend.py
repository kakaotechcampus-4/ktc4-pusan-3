"""식단 후보 제출 인자. suggestion form에 맞춰야 화면에서 보여줄 수 있음

모델은 코드가 보여 준 후보 목록에서 `menu_key` 만 고른다. 메뉴 이름과 재료는 모델이 쓰지 않는다 —
코드가 카탈로그 행에서 채운다. 모델이 "바나나 셰이크" 를 내면서 재료에 우유를 빠뜨려도 사후 필터가
그대로 통과시키는 일이 없게 하려는 것이다. MealCandidate는 suggestion의 reason · 근거로 옮겨진다.
evidence가 비면 일반 추천(또래 기준)으로 분류된다.

예외 하나 — 보호자가 재료 · 요리 종류를 정해 물었는데(`requested_food`, 보호자 말에 있을 때만 인정)
레시피북에 맞는 메뉴가 모자랄 때만 모델이 흔한 메뉴를 직접 낸다(`generated`, #740). 이때는 이름 ·
재료를 모델이 쓰므로 코드가 안전 필터(이름 + 재료)와 형식(재료 8개 이하, 숫자 · 단위 없음)을 건다.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.food.schemas.common import EvidenceRef, MealSlot, ToolArgs


class MealCandidate(ToolArgs):
    menu_key: Annotated[
        str,
        Field(
            min_length=1,
            description="보여 준 후보 목록에 있는 menu_key 만. 지어내거나 고쳐 쓰지 않는다",
        ),
    ]
    meal_slot: Annotated[MealSlot, Field(description="breakfast / lunch / dinner / snack")]
    reason: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "이 아이에게 맞는 이유와 지금인 이유를 한두 문장으로. "
                "조회한 기록에 있는 내용으로만 쓴다. "
                "싫어하는 것을 피해 골랐으면 무엇을 피했는지 쓴다. "
                "수치와 몸 상태는 쓰지 않는다. 보호자 화면에 그대로 나간다"
            ),
        ),
    ]
    evidence: Annotated[
        list[EvidenceRef],
        Field(
            default_factory=list,
            description="근거로 쓴 기록. 조회 결과에 있던 것만. 근거가 없으면 비운다",
        ),
    ]


_NAME = Annotated[str, Field(min_length=1, max_length=15)]


class GeneratedMeal(ToolArgs):
    """레시피북에 없는 흔한 메뉴. 보호자가 정한 조건이 인정되고 레시피북이 모자랄 때만 낸다."""

    name: Annotated[
        str,
        Field(
            min_length=2,
            max_length=20,
            description=(
                "집이나 식당에서 흔히 먹는 메뉴 이름. 조건을 억지로 끼운 조합이나 지어낸 이름은 "
                "쓰지 않는다"
            ),
        ),
    ]
    main_ingredients: Annotated[
        list[_NAME],
        Field(
            min_length=1,
            max_length=3,
            description="주재료 1~3개. 이름만. 양·숫자·단위는 쓰지 않는다",
        ),
    ]
    ingredients: Annotated[
        list[_NAME],
        Field(
            min_length=1,
            max_length=8,
            description=(
                "들어가는 재료 전부, 최대 8개. 간장·된장·고추장·버터·마요네즈 같은 양념도 적는다 — "
                "빠뜨리면 알레르기 확인이 놓친다. 이름만. 양·숫자·단위·조리법은 쓰지 않는다"
            ),
        ),
    ]
    meal_slot: Annotated[MealSlot, Field(description="breakfast / lunch / dinner / snack")]
    reason: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "이 아이에게 맞는 이유를 한두 문장으로. 조회한 기록에 있는 내용으로만 쓴다. "
                "수치와 몸 상태는 쓰지 않는다. 보호자 화면에 그대로 나간다"
            ),
        ),
    ]
    evidence: Annotated[
        list[EvidenceRef],
        Field(
            default_factory=list,
            description="근거로 쓴 아이 기록. 조회 결과에 있던 것만. 근거가 없으면 비운다",
        ),
    ]


class ProposeMealCandidatesArgs(ToolArgs):
    # 후보 풀이 처음부터 모자라면 모델이 고를 수 있는 것도 모자라서 1개부터 받는다.
    # 채울 수 있는데 덜 낸 것은 출력 tool 의 check_count 가 막는다 (Tool_공통.md §5-2)
    requested_food: Annotated[
        str | None,
        Field(
            default=None,
            max_length=20,
            description=(
                "보호자가 재료나 요리 종류를 정해 물었을 때만, 보호자 말 그대로 적는다. "
                "예: 파프리카, 태국음식. 막연한 요청이면 비운다"
            ),
        ),
    ]
    requested_kind: Annotated[
        Literal["ingredient", "cuisine"] | None,
        Field(
            default=None,
            description="requested_food 가 재료면 ingredient, 요리 종류면 cuisine. 비웠으면 비운다",
        ),
    ]
    candidates: Annotated[
        list[MealCandidate],
        Field(
            default_factory=list,
            max_length=MAX_SUGGESTIONS,
            description=(
                f"후보 목록에서 고른 식사·간식. 합쳐서 최대 {MAX_SUGGESTIONS}개 "
                "(generated 와 합친 개수)"
            ),
        ),
    ]
    generated: Annotated[
        list[GeneratedMeal],
        Field(
            default_factory=list,
            max_length=MAX_SUGGESTIONS,
            description=(
                "후보 목록의 '요청과 맞음' 메뉴로 모자랄 때만 직접 내는 흔한 메뉴. "
                "requested_food 를 적었을 때만 쓴다"
            ),
        ),
    ]

    @model_validator(mode="after")
    def _count(self) -> "ProposeMealCandidatesArgs":
        total = len(self.candidates) + len(self.generated)
        if not 1 <= total <= MAX_SUGGESTIONS:
            raise ValueError(
                f"candidates 와 generated 를 합쳐 1~{MAX_SUGGESTIONS}개를 낸다 (지금 {total}개)"
            )
        return self
