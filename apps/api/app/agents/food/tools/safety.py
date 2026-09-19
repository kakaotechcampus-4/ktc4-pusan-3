"""알레르기 및 식이 제한을 확인하는 내부 safety tool.

필요한 처리 단계에서 코드가 직접 실행한다.
사용 위치:
- 식단 추천: 모델 호출 전 안전 정보 확인, 추천 후보 생성 후 최종 필터링
  안전 정보를 확인할 수 없으면 추천을 진행하지 않는다.
- 급식 조회: 메뉴의 주의 식품 표시
  안전 정보 조회에 실패하면 메뉴는 반환하되 확인하지 못한 상태임을 함께 표시한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.food.store.ports import SafetyEntry


@dataclass(frozen=True)
class SafetyHit:
    item: str  # 주의가 필요한 재료 또는 메뉴
    kind: str  # allergy / chronic_disease / dietary_restriction
    label: str  # 매칭된 health_safety.label


def filter_food_safety(
    items: Sequence[str],
    entries: Sequence[SafetyEntry],
) -> list[SafetyHit]:
    """재료나 메뉴를 health_safety 정보와 비교해 주의 항목을 반환한다.

    구현:
    - allergy, chronic_disease, dietary_restriction 항목만 검사한다.
      비활성 항목은 조회 단계에서 제외한다.
    - label, aliases, restricted_foods를 비교에 사용한다.
      문자열을 정규화한 뒤 부분 일치 여부를 확인한다.
    - 만성질환은 보호자가 등록한 restricted_foods만 사용한다.
      질환명만 보고 제한 식품을 추가로 추론하지 않는다.
    - 주의 항목이 포함된 후보는 수정하지 않고 제외한다.
    - 등록되지 않은 알레르기나 제한 사항을 새로 추론하지 않는다.
    - 로그에는 매칭 건수만 남기고 label 원문은 기록하지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")
