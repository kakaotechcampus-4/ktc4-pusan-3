"""도메인 쓰기가 실제로 commit된 run인지 기록한다.

쓰기 성공 후 실패한 run이 재전송되어 같은 작업이 중복되는 것을 막기 위해 사용한다.
Agent는 이 상태를 모르며 entrypoint가 쓰기 포트를 감싸서 관리한다.

[감싸는 기준] 쓰기 포트를 더할 때 이 기준으로 선택:
- 감싼다: 보호자 말을 반영한 쓰기. 급식 수정·삭제, Health 복약 기록.
- 감싸지 않는다: 요청하지 않아도 계산 중에 생기는 영양 구간(`bands.save`)·메뉴 카탈로그
  (`catalog.put`). 감싸면 질문만 한 run이 모델 실패에도 done이 되어 원문이 입력창에 돌아가지 않는다.
  대신 다시 보내도 안전해야 해서, 덮어쓰기면 그대로 두고, 쌓이는 쓰기(INSERT)면 감싸지 않고
  중복 방지키를 둔다(지금은 없음). 메뉴 카탈로그를 "카탈로그에 없는 메뉴(캐시 미스)만
  넘기고 백엔드가 채우는" 안으로 정하면 그 menu_key를 쌓는 큐가 그 예이고, menu_key
  unique로 막는다.
  덮어쓰기인지는 감쌀지를 가르는 기준이 아니다(급식 수정도 덮어쓰기지만 감싼다).

한계: commit 응답을 기다리는 중에 취소되면 실제로는 들어갔어도 표시가 남지 않는다.
다시 보내도 안전하게 하는 건 쓰기 쪽 몫 — UPDATE는 같은 갱신을 두 번 적용해도
결과가 같게, INSERT는 도메인 값으로 만든 중복 방지키로 막는다(포트 계약).
"""

from dataclasses import replace
from datetime import date
from uuid import UUID

from app.agents.food.store.ports import DaycareMealRow, DaycareMealStore, FoodPorts


class RunWrites:
    """run 하나 동안 보호자 말을 반영한 도메인 쓰기가 성공한 적이 있는지."""

    def __init__(self) -> None:
        self.wrote = False

    def mark(self) -> None:
        self.wrote = True


class _RecordedDaycareMeals:
    def __init__(self, inner: DaycareMealStore, writes: RunWrites) -> None:
        self._inner = inner
        self._writes = writes

    async def rows(self, *, child_id: UUID, date_from: date, date_to: date) -> list[DaycareMealRow]:
        return await self._inner.rows(child_id=child_id, date_from=date_from, date_to=date_to)

    async def has_rows(self, *, child_id: UUID) -> bool:
        return await self._inner.has_rows(child_id=child_id)

    async def update(self, row: DaycareMealRow) -> None:
        await self._inner.update(row)
        self._writes.mark()  # 돌아온 뒤에만. 예외면 여기 오지 않는다

    async def delete(self, *, child_id: UUID, row_ids: tuple[UUID, ...]) -> None:
        await self._inner.delete(child_id=child_id, row_ids=row_ids)
        self._writes.mark()


def record_food_writes(ports: FoodPorts, writes: RunWrites) -> FoodPorts:
    """Food 에서 보호자 말을 반영한 쓰기 포트(급식)만 감싼다."""
    return replace(ports, daycare=_RecordedDaycareMeals(ports.daycare, writes))
