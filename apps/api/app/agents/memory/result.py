"""Memory tool 실행 결과.

봉투(`ToolResult`)와 공통 실패 코드는 `common/tool_runtime.py` 가 정본이다. 여기서는
Memory 만 쓰는 코드를 얹고 `Operation` 값 집합을 좁힌다.

db write가 필요한 tool은 구조화된 스키마를 리턴.
모델은 error.code 를 보고 다음 행동을 정하므로 코드 문자열을 임의로 만들지 않는다.
"""

from collections.abc import Callable, Sequence
from typing import Any, Literal

from app.agents.common.tool_runtime import ErrorCode as CommonErrorCode
from app.agents.common.tool_runtime import ToolResult
from app.agents.common.tool_runtime import fail as _fail
from app.agents.common.tool_runtime import ok as _ok

Operation = Literal["parse", "create", "query", "update", "delete"]

__all__ = [
    "MAX_ROWS",
    "ErrorCode",
    "Operation",
    "ToolResult",
    "cut",
    "fail",
    "nearest",
    "ok",
    "recent",
]

# 조회 tool 이 종류마다 돌려주는 행 수의 상한 (#535). 조회 · 삭제 카드의 탭 하나에 실리는 양이고,
# 보호자가 한 화면에서 보고 판단할 수 있는 양이다. 보이지 않던 기록은 삭제 후보가 아니다
MAX_ROWS = 40

# 잘린 조회 결과에만 싣는다. 잘리지 않은 결과는 이전과 같은 모양이다 — 프롬프트에 규칙을 더하거나
# 늘 truncated 를 실으면 "아니, 그냥 둬" 에도 update 를 부르는 일이 생겼다(라이브 D02, 16번 중 3번)
_CUT_NOTICE = (
    f"최근 {MAX_ROWS}건만 돌려줬다. 그보다 앞선 기록이 더 있지만 보이지 않는다. "
    "조회 결과를 알릴 때는 최근 기록까지만 봤다고 말하고 기간을 좁혀 달라고 한다."
)
# 일정은 오늘에서 가까운 것을 남긴다 — 앞뒤로 더 있을 수 있어서 "최근" 이라고 하지 않는다
_EVENT_CUT_NOTICE = (
    f"오늘에서 가까운 일정 {MAX_ROWS}건만 돌려줬다. 그보다 먼 일정이 더 있지만 보이지 않는다. "
    "조회 결과를 알릴 때는 가까운 일정까지만 봤다고 말하고 기간을 좁혀 달라고 한다."
)


class ErrorCode(CommonErrorCode):
    """공통 코드 + Memory 전용.

    Memory 의 되묻기는 대상마다 다음 행동이 달라 공통 `NOT_FOUND` 로 뭉치지 않는다.
    """

    TARGET_REQUIRED = "TARGET_REQUIRED"  # 조회 tool 을 먼저 호출
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"  # 사용자에게 확인
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"  # 어느 것인지 되물음
    UNKNOWN_EVENT = "UNKNOWN_EVENT"  # create_event / query_event 를 먼저
    DATE_UNPARSEABLE = "DATE_UNPARSEABLE"  # 날짜 표현을 사용자에게 되물음
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # 범위 밖이라고 안내
    NOT_CONFIRMED = "NOT_CONFIRMED"  # 보호자에게 카드로 보여 준 기록 밖의 삭제 (#535)
    NOT_CHILD_RECORD = "NOT_CHILD_RECORD"  # 아이 기록인지 원문에 없음. 저장하지 않고 묻거나 알림
    HAZARD = "HAZARD"  # 음식이 아닌 위험 물질. 저장하지 않고 상태 확인 안내 (#626)
    REGISTERED_MEDICINE = "REGISTERED_MEDICINE"  # 진행 중 복약 일정의 약. 체크는 복약 화면이 한다
    SAFETY_RECORD = "SAFETY_RECORD"  # 알레르기 · 만성질환 이름. 저장하지 않고 안내는 pipeline 이


def ok(operation: Operation, resource: str, **data: Any) -> ToolResult:
    return _ok(operation, resource, **data)


def fail(operation: Operation | None, resource: str, code: str, message: str) -> ToolResult:
    # message는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.
    return _fail(operation, resource, code, message)


def recent[T](rows: Sequence[T]) -> tuple[list[T], bool]:
    """오래된 순 관찰 조회 결과에서 최근 MAX_ROWS 건과 잘렸는지. 순서는 store 가 지킨다(ports).

    순서는 그대로 둔다. 보호자에게 보여 줄 수 있는 것이 이만큼이라 전체 건수는 돌려주지 않는다 —
    결과에 넣으면 모델이 그 숫자로 "N건 삭제할까요?" 를 써서 카드와 어긋난다.
    """
    return list(rows[-MAX_ROWS:]), len(rows) > MAX_ROWS


def nearest[T](rows: Sequence[T], distance: Callable[[T], float]) -> tuple[list[T], bool]:
    """일정 조회 결과에서 오늘에 가까운 MAX_ROWS 건과 잘렸는지. 남긴 것은 원래 순서 그대로다.

    일정은 시작 시각 오름차순이라 뒤에서 자르면 가장 먼 미래만 남는다 — 이번 주 일정이 빠진다.
    """
    if len(rows) <= MAX_ROWS:
        return list(rows), False
    keep = set(sorted(range(len(rows)), key=lambda i: distance(rows[i]))[:MAX_ROWS])
    return [row for i, row in enumerate(rows) if i in keep], True


def cut(truncated: bool, *, event: bool = False) -> dict[str, Any]:
    """조회 결과에 더할 잘림 표시. 잘리지 않았으면 아무것도 더하지 않는다."""
    if not truncated:
        return {}
    return {"truncated": True, "notice": _EVENT_CUT_NOTICE if event else _CUT_NOTICE}
