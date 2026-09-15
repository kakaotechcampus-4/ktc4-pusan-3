"""기관 급식 조회 tool. 아이에게 위험한 식품 표시는 filter_food_safety(코드)를 재사용한다."""

from app.agents.food.context import FoodContext
from app.agents.food.result import ToolResult
from app.agents.food.schemas.menu import DaycareMenuArgs


async def lookup_daycare_menu(context: FoodContext, args: DaycareMenuArgs) -> ToolResult:
    """기관 급식 메뉴를 날짜 기준으로 조회하고, 주의가 필요한 식품을 표시한다.

    DB 연결 후:
    - args.day를 날짜로 변환한다.
    - context.menu.menu(child_id, day)에서 메뉴, 알레르기 정보, 총 열량, 단백질을 가져온다.
    해당 날짜의 데이터가 없으면 메뉴 없음으로 처리한다.
    - context.safety.food_safety(child_id) 결과를 기준으로 위험 식품을 표시한다.
    안전 정보 조회에 실패한 경우에는 "알레르기 확인 못 함" 상태를 함께 반환한다.
    - 결과에는 메뉴, 위험 표시, 영양 합계만 포함한다.
    알레르기 label 원문은 로그에 남기지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")
