"""registry 가 tool 을 여닫을 때 보는 값.

전부 **발화가 아니라 아이 데이터·환경에서** 온다. 보호자가 "우리 애 두 돌"이라고 말해도
게이팅은 `stage.months` 를 쓴다 (docs/agents/shared/Tool_공통.md §3).

성별은 축이 아니다. 수집도 사용도 하지 않는다.
"""

from dataclasses import dataclass, field
from typing import Literal

from app.rules.age import LifeStage

AllergyStatus = Literal["none", "has", "unknown"]


@dataclass(frozen=True)
class DataReady:
    """그 아이에게 그 데이터가 있는가. 없으면 그 데이터를 읽는 tool 이 빠진다."""

    daycare_meal: bool = False  # 급식 행 — Food 의 급식 조회·갱신
    notice: bool = False  # 기관 공지 — Growth 의 lookup_notice
    book_api: bool = True  # 도서 API 가용 — Growth 의 도서 라벨


@dataclass(frozen=True)
class Gate:
    """`tools_for(task_type, gate)` 의 입력.

    `stage` 가 월령을 들고 있으므로 `age_months` · `corrected_months` 를 따로 넘기지 않는다.
    """

    stage: LifeStage
    consent_child_health: bool  # consent(scope=child_health) 최신 행이 granted 인가
    safety_ok: bool  # health_safety 조회에 성공했는가 (실패는 0행과 다르다)
    allergy_status: AllergyStatus  # child.allergy_status — 0행의 뜻을 가른다 (F-4)
    safety_row_count: int = 0  # health_safety 등록 건수
    growth_log_count: int = 0  # child_growth_log 측정 건수
    has_location: bool = False
    outdoor_ok: bool = True
    data: DataReady = field(default_factory=DataReady)

    @property
    def allergy_filter_ready(self) -> bool:
        """알레르기 필터를 걸 수 있는가. Food 의 식단 추천이 이 값으로 열리고 닫힌다.

        0행이 "없다고 확인함"인지 "물어본 적 없음"인지는 행으로 표현할 수 없다.
        그래서 `child.allergy_status` 를 함께 본다 (F-4).

            none    0행이어도 연다 — 없다고 확인한 아이다
            has     1행 이상이면 연다. 0행이면 막는다 (있다는데 뭔지 모른다)
            unknown 막는다 — 물어본 적이 없다
        """
        if not self.safety_ok:
            return False
        if self.allergy_status == "none":
            return True
        if self.allergy_status == "has":
            return self.safety_row_count > 0
        return False
