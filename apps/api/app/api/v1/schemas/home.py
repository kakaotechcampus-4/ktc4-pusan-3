"""홈 응답 — #259 (03 홈).

모양은 화면 타입(apps/web `lib/api/types.ts` 의 `HomeResponse`)을 따른다.
`today` 는 오늘 급식(끼니마다 한 줄) 다음에 오늘 일정이 온다. 급식 줄은 #259 때 급식 표가
없어서 뺐다가 급식표 사진 저장(`daycare_meal`)이 생긴 뒤 붙였다.
"""

from typing import Annotated, Literal

from pydantic import Field

from app.api.schema_base import ApiSchema
from app.api.v1.schemas.common import Ref
from app.rules.home_prompts import PromptKey


class TodayMealOut(ApiSchema):
    """오늘 급식 한 끼. 보호자가 저장한 급식표(`daycare_meal`)를 있는 그대로 보여 준다.

    🚨 알레르기 판정 · "괜찮아요" 같은 문구 · 추천을 싣지 않는다 (루트 CLAUDE.md §2).
    """

    kind: Literal["meal"] = "meal"
    title: str = Field(
        description=(
            '끼니 이름 · 메뉴. 예) "점심 · 잡곡밥, 소고기미역국". '
            "메뉴는 저장된 메뉴 키(menu_key)다 — 급식표 글자에서 띄어쓰기 · 괄호를 뺀 이름이라 "
            "급식표와 모양이 조금 다를 수 있다"
        )
    )
    origin: str = Field(
        description=(
            '어디서 온 급식인지 보여 주는 문구. 급식표 사진에서 읽은 것이면 "기관 급식표", '
            '지역 표준식단이면 "지역 표준식단 · 실제 급식과 다를 수 있어요"'
        )
    )


class TodayEventOut(ApiSchema):
    kind: Literal["event"] = "event"
    event_id: str
    title: str


TodayItem = Annotated[TodayMealOut | TodayEventOut, Field(discriminator="kind")]


class HighlightOut(ApiSchema):
    text: str = Field(description="눈여겨볼 기억의 이름(merge_key)")
    state_reason: str = Field(description="서버 문구. 기억 목록의 state_reason 과 같다")
    ref: Ref


class AgentPromptOut(ApiSchema):
    agent: Literal["food", "activity"]
    text: str = Field(description='버튼에 보이는 문구. 예) "민준이가 먹을 저녁 추천해드릴까요?"')
    prompt_key: PromptKey = Field(
        description=(
            "버튼을 누르면 추천 요청에 agents 와 함께 그대로 보내는 키. "
            "서버가 이 키로 Agent 에 넘길 요청 문장을 정한다 (rules/home_prompts.py)"
        )
    )


class HomeResponse(ApiSchema):
    observation_count: int = Field(
        description="기록 수. 기록 탭처럼 deleted 만 빼고 센다. 건강 기록도 센다"
    )
    week_count: int = Field(description="이번 주(월요일 시작, 한국 시각)에 일어난 기록 수")
    upcoming_count: int = Field(description="지금부터 7일 안에 있는 일정 수")
    today: list[TodayItem] = Field(
        description=(
            "오늘(한국 시각) 급식 줄 다음에 일정 줄. 급식은 오전 간식 → 점심 → 오후 간식 순이고, "
            "메뉴가 없는 끼니 · 급식이 없는 날은 줄이 없다"
        )
    )
    highlight: HighlightOut | None = Field(description="기억이 없으면 null")
    agent_prompts: list[AgentPromptOut] = Field(
        description="시간대 · 주중/주말로 정한다. 개인화하지 않는다"
    )
