"""기억 목록 응답 — #259 (기억 탭).

모양은 화면 타입(apps/web `lib/api/types.ts` 의 `AffinitiesResponse` · `Affinity`)을 따른다.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.api.v1.schemas.common import Ref


class AffinityOut(BaseModel):
    kind: Literal["profile_affinity"] = "profile_affinity"
    id: str
    merge_key: str
    domain: Literal["food", "activity", "growth"] = Field(
        description="화면이 쓰는 Agent 값. 기억 표의 education 은 growth 로 내려간다"
    )
    state: Literal["candidate", "confirmed", "archived"] = Field(
        description="상태는 Curator 규칙만 바꾼다. 목록은 기본으로 archived 를 뺀다"
    )
    polarity: int | None = Field(
        description="-1 싫어함 · 0 갈림 · 1 좋아함. candidate 면 null 일 수 있다"
    )
    strength: float
    last_observed_on: date
    observation_count: int = Field(description="이 기억에 묶인 active 관찰 수")
    state_reason: str = Field(
        description='서버 문구. 예) "지금까지 4번 기록됐고, 마지막은 어제예요"'
    )
    is_stale: bool = Field(
        description="마지막 기록이 6개월 이상 전 — 단독 근거로 쓰지 않는다 (NF-08)"
    )
    source_refs: list[Ref] = Field(description="묶인 active 관찰. 최근 것이 앞")


class AffinitiesResponse(BaseModel):
    affinities: list[AffinityOut]
    total: int = Field(
        description=(
            "필터 · 날짜 · 검색을 건 뒤 `affinities` 의 전체 건수. 쪽과 무관하다. "
            "같은 응답의 `safety` 는 세지 않는다 — `safety` 는 쪽으로 나누지 않는다"
        )
    )
    page: int = Field(description="요청한 쪽 번호 (1부터). `affinities` 를 나눈 쪽이다")
    size: int = Field(description="요청한 한 쪽 개수. `affinities` 에만 적용한다")
    total_pages: int = Field(
        description="`affinities` 의 전체 쪽 수 — ceil(total / size). 0건이면 0"
    )
    safety: list[dict[str, Any]] = Field(
        default_factory=list,
        description="알레르기 · 건강 정보. 첫 배포 범위 밖이라 늘 빈 배열이다 (#259)",
    )
