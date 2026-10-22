"""Health tool 실행 결과.

봉투(`ToolResult`)와 실패 코드는 `common/tool_runtime.py` 가 정본이다. Health 전용 코드는 아직
없어서 공통 `ErrorCode` 를 그대로 상속해 둔다 (Growth `result.py` 와 같은 모양 — 패키지끼리
import 하지 않아 사본이다).
"""

from typing import Any, Literal

from app.agents.common.tool_runtime import ErrorCode as CommonErrorCode
from app.agents.common.tool_runtime import ToolResult
from app.agents.common.tool_runtime import fail as _fail
from app.agents.common.tool_runtime import ok as _ok

# query = 조회, draft = 초안(DB 를 건드리지 않는다), record = 복용 기록 · 중단처럼 Agent 가 쓰는 것
Operation = Literal["query", "draft", "record"]

__all__ = ["ErrorCode", "Operation", "ToolResult", "fail", "ok"]


class ErrorCode(CommonErrorCode):
    """Health 만의 실패 코드. 값은 덮어쓰지 않고 더한다."""

    NO_CHANGE = "NO_CHANGE"  # 수정인데 지금 값과 같다 → 카드를 띄우지 않는다


def ok(operation: Operation, resource: str, **data: Any) -> ToolResult:
    return _ok(operation, resource, **data)


def fail(operation: Operation | None, resource: str, code: str, message: str) -> ToolResult:
    # message 는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.
    # 발화 원문 · 약명 · 증상을 넣지 않는다 — 로그로 흘러간다
    return _fail(operation, resource, code, message)
