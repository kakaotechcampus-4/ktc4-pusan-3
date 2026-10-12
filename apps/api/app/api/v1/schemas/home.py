"""홈 응답 — #259 (03 홈).

모양은 화면 타입(apps/web `lib/api/types.ts` 의 `HomeResponse`)을 따른다.
다른 점: `today` 는 일정 줄만이다 (급식 줄은 범위 밖).
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.api.v1.schemas.common import Ref
from app.rules.home_prompts import PromptKey


class TodayEventOut(BaseModel):
    kind: Literal["event"] = "event"
    event_id: str
    title: str


class HighlightOut(BaseModel):
    text: str = Field(description="눈여겨볼 기억의 이름(merge_key)")
    state_reason: str = Field(description="서버 문구. 기억 목록의 state_reason 과 같다")
    ref: Ref


class AgentPromptOut(BaseModel):
    agent: Literal["food", "activity"]
    text: str = Field(description='버튼에 보이는 문구. 예) "민준이가 먹을 저녁 추천해드릴까요?"')
    prompt_key: PromptKey = Field(
        description=(
            "버튼을 누르면 추천 요청에 agents 와 함께 그대로 보내는 키. "
            "서버가 이 키로 Agent 에 넘길 요청 문장을 정한다 (rules/home_prompts.py)"
        )
    )


class HomeResponse(BaseModel):
    observation_count: int = Field(
        description="기록 수. 기록 탭처럼 deleted 만 빼고 센다. 건강 기록은 세지 않는다"
    )
    week_count: int = Field(description="이번 주(월요일 시작, 한국 시각)에 일어난 기록 수")
    upcoming_count: int = Field(description="지금부터 7일 안에 있는 일정 수")
    today: list[TodayEventOut] = Field(description="오늘 일정")
    highlight: HighlightOut | None = Field(description="기억이 없으면 null")
    agent_prompts: list[AgentPromptOut] = Field(
        description="시간대 · 주중/주말로 정한다. 개인화하지 않는다"
    )
