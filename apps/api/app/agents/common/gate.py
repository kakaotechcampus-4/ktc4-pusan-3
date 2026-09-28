"""registry 가 tool 을 여닫을 때 보는 값.

전부 **발화가 아니라 아이 데이터·환경에서** 온다. 보호자가 "우리 애 두 돌"이라고 말해도
게이팅은 `stage.months` 를 쓴다 (docs/agents/shared/Tool_공통.md §3).

성별은 축이 아니다. 수집도 사용도 하지 않는다.
"""

from dataclasses import dataclass, field
from typing import Literal

from app.rules.age import LifeStage

# `health_safety.state`. child 를 만들 때 `allergen_term` 19종이 전부 unknown 으로 들어가고,
# 보호자가 온보딩에서 답하면 none/active 로 바뀐다. 19종 밖은 추가할 때 active 로 들어간다.
#
# **거르는 것은 active 뿐이다.** unknown 은 추천을 막지 않고 "이 알레르기가 있는지
# 확인해 주세요" 안내만 붙인다 — 모르는 항목 때문에 추천을 통째로 닫으면
# 답을 미룬 보호자가 서비스를 못 쓴다. 건강정보 동의를 안 해 0행인 경우도 같다.
SafetyState = Literal["active", "retracted", "none", "unknown"]


@dataclass(frozen=True)
class DataReady:
    """그 아이에게 그 데이터가 있는가. 없으면 그 데이터를 읽는 tool 이 빠진다."""

    daycare_meal: bool = False  # 급식 행 — Food 의 급식 조회·갱신
    notice: bool = False  # 기관 공지 — Growth 의 lookup_notice
    book_api: bool = True  # 도서 API 가용 — Growth 의 도서 라벨


@dataclass(frozen=True)
class Gate:
    """`tools_for(task_type, gate)` 의 입력.

    `stage` 가 월령을 들고 있으므로 `age_months` 를 따로 넘기지 않는다.
    """

    stage: LifeStage
    consent_child_health: bool  # consent(scope=child_health) 최신 행이 granted 인가.
    # False 면 health_safety·child_growth_log 를 못 읽는다 — allergy_states 가 빈 튜플로 온다.
    # 조회 실패가 아니라 읽을 것이 없는 상태다. Food 는 닫히지 않고 Health 는 전 라벨이 닫힌다.
    safety_ok: bool  # health_safety 조회에 성공했는가 (실패는 0행과 다르다)
    allergy_states: tuple[SafetyState, ...] = ()  # kind='allergy' 행들의 state (F-4)
    growth_log_count: int = 0  # child_growth_log 측정 건수
    has_location: bool = False
    outdoor_ok: bool = True
    data: DataReady = field(default_factory=DataReady)

    @property
    def allergy_filter_ready(self) -> bool:
        """알레르기 필터를 걸 수 있는가. Food 의 식단 추천이 이 값으로 열리고 닫힌다.

        **막는 것은 조회 실패뿐이다.** 실패를 빈 목록으로 숨기지 않는다 (루트 CLAUDE.md §2).

        0행도 연다. 건강정보 동의를 안 한 보호자는 `health_safety` 에 행이 쌓이지 않는데,
        그렇다고 식단을 못 받으면 안 된다 — 알레르기 없는 아이 기준의 일반 식단으로 간다.
        `active` 행이 없으니 거를 것도 없다.

        `unknown` 도 막지 않는다. 아직 안 물어본 항목일 뿐이고, 추천은 내보내되
        `allergy_unconfirmed` 로 확인 안내를 함께 띄운다 (F-4).
        `retracted` 는 필터에서 `none` 과 같다 — 보호자가 취소했고 지금은 해당 없음이다.
        """
        return self.safety_ok

    @property
    def allergy_unconfirmed(self) -> bool:
        """아직 안 물어본 알레르기 항목이 있는가.

        참이면 추천과 함께 "이 알레르기가 있는지 확인해 주세요" 안내를 띄운다.
        항목 이름이 필요하면 tool 이 `state='unknown'` 행을 따로 조회한다 — Gate 는 상태만 든다.
        """
        return "unknown" in self.allergy_states
