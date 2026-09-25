"""Memory tool 실행 결과.

봉투(`ToolResult`)와 공통 실패 코드는 `common/tool_runtime.py` 가 정본이다. 여기서는
Memory 만 쓰는 코드를 얹고 `Operation` 값 집합을 좁힌다.

db write가 필요한 tool은 구조화된 스키마를 리턴.
모델은 error.code 를 보고 다음 행동을 정하므로 코드 문자열을 임의로 만들지 않는다.
"""

from typing import Any, Literal

from app.agents.common.tool_runtime import ErrorCode as CommonErrorCode
from app.agents.common.tool_runtime import ToolResult
from app.agents.common.tool_runtime import fail as _fail
from app.agents.common.tool_runtime import ok as _ok

Operation = Literal["parse", "create", "query", "update", "delete"]

__all__ = ["ErrorCode", "Operation", "ToolResult", "fail", "ok"]


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


def ok(operation: Operation, resource: str, **data: Any) -> ToolResult:
    return _ok(operation, resource, **data)


def fail(operation: Operation | None, resource: str, code: str, message: str) -> ToolResult:
    # message는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.
    return _fail(operation, resource, code, message)
