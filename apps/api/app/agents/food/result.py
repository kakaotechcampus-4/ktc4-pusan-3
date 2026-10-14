"""Food tool 실행 결과.

봉투(`ToolResult`)와 실패 코드는 `common/tool_runtime.py` 가 정본이다. Food 는 전용 코드가
없어 `Operation` 값 집합만 좁힌다.
"""

from typing import Any, Literal

from app.agents.common.tool_runtime import ErrorCode, ToolResult
from app.agents.common.tool_runtime import fail as _fail
from app.agents.common.tool_runtime import ok as _ok

# query = 조회, propose = 식단 후보 제출, report = 영양소 분석 보고,
# update · delete = 급식 행 갱신 · 삭제(Food 가 보호자 말을 반영해 쓰는 유일한 자리)
Operation = Literal["query", "propose", "report", "update", "delete"]

__all__ = ["ErrorCode", "Operation", "ToolResult", "fail", "ok"]


def ok(operation: Operation, resource: str, **data: Any) -> ToolResult:
    return _ok(operation, resource, **data)


def fail(operation: Operation | None, resource: str, code: str, message: str) -> ToolResult:
    # message는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.
    return _fail(operation, resource, code, message)
