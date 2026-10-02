"""기관 급식 조회·갱신·삭제 tool. 아이에게 위험한 식품 표시는 filter_food_safety(코드)를 재사용"""

from app.agents.food.context import FoodContext
from app.agents.food.result import ToolResult
from app.agents.food.schemas.daycare import DeleteDaycareMealArgs, UpdateDaycareMealArgs
from app.agents.food.schemas.menu import DaycareMenuArgs


async def lookup_daycare_menu(context: FoodContext, args: DaycareMenuArgs) -> ToolResult:
    """기관 급식 메뉴를 날짜 기준으로 조회하고, 주의가 필요한 식품을 표시한다.

    DB 연결 후:
    - args.day를 날짜로 변환한다.
    - context.menu.menu(child_id, day)에서 메뉴, 알레르기 정보, 총 열량, 단백질을 가져온다.
    해당 날짜의 데이터가 없으면 메뉴 없음으로 처리한다.
    - build_gate가 읽어 둔 context.state.safety로 filter_food_safety를 불러 위험 식품을 표시한다.
    포트를 다시 읽지 않는다. None(조회 실패)이면 "알레르기 확인 못 함" 상태를 함께 반환한다.
    - 결과에는 메뉴, 위험 표시, 영양 합계만 포함한다.
    알레르기 label 원문은 로그에 남기지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")


async def update_daycare_meal(context: FoodContext, args: UpdateDaycareMealArgs) -> ToolResult:
    """이미 있는 급식 행을 고친다. 없는 급식을 새로 만들지 않는다.

    DB 연결 후:
    - resolve_meal_date 로 date_span → 날짜. 과거 30일 밖이면 거절.
    - removed_spans·added_spans 를 resolve_menu 로 menu_key 로 바꿔 menu_keys 를 교체한다.
      넣을 메뉴만 있고 뺄 메뉴가 없으면 되묻는다.
    - amount_span 이 있으면 사전으로 amount_factor·amount_known 을 갱신한다.
    - 갱신한 메뉴는 context.state.safety로 filter_food_safety를 다시 거친다. 걸려도 저장은
      막지 않고 경고만 낸다.
    """
    raise NotImplementedError("DB 연결 후 구현")


async def delete_daycare_meal(context: FoodContext, args: DeleteDaycareMealArgs) -> ToolResult:
    """그날 급식 행을 지운다(결식 처리).

    DB 연결 후:
    - resolve_meal_date 로 date_span → 날짜.
    - slot 이 없으면 그날 행 전부를 지운다. 있으면 그 슬롯만.
    - 대상 날짜가 모호하면 반드시 되묻는다 — 되돌리려면 OCR 재적재가 필요하다.
    """
    raise NotImplementedError("DB 연결 후 구현")
