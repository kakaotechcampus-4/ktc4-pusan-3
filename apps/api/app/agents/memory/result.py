"""tool 실행 결과의 공통 반환 규격.

db write가 필요한 tool은 구조화된 스키마를 리턴.
모델은 error.code 를 보고 다음 행동을 정하므로 코드 문자열을 임의로 만들지 않는다.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

Operation = Literal["parse", "create", "query", "update", "delete"]


class ErrorCode:
    """tool 이 돌려줄 수 있는 실패 사유. 각 코드는 모델이 할 다음 행동과 1:1 이다."""

    VALIDATION_ERROR = "VALIDATION_ERROR"      # 인자를 고쳐 다시 호출
    TARGET_REQUIRED = "TARGET_REQUIRED"        # 조회 tool 을 먼저 호출
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"      # 사용자에게 확인
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"      # 어느 것인지 되물음
    UNKNOWN_EVENT = "UNKNOWN_EVENT"            # create_event / query_event 를 먼저
    DATE_UNPARSEABLE = "DATE_UNPARSEABLE"      # 날짜 표현을 사용자에게 되물음
    OUT_OF_SCOPE = "OUT_OF_SCOPE"              # 범위 밖이라고 안내
    UNKNOWN_TOOL = "UNKNOWN_TOOL"              # 존재하지 않는 tool 이름


@dataclass(frozen=True)
class ToolResult:
    success: bool
    operation: Operation
    resource: str    # observation_* / event / reminder ...
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, str] | None = None

    def to_payload(self) -> dict[str, Any]:
        """모델에게 role="tool" 로 돌려줄 dict. 실패면 data 를 싣지 않는다."""
        payload: dict[str, Any] = {
            "success": self.success,
            "operation": self.operation,
            "resource": self.resource,
        }
        if self.success:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
        return payload


def ok(operation: Operation, resource: str, **data: Any) -> ToolResult:
    return ToolResult(success=True, operation=operation, resource=resource, data=data)


def fail(operation: Operation, resource: str, code: str, message: str) -> ToolResult:
    # message는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.
    return ToolResult(
        success=False,
        operation=operation,
        resource=resource,
        error={"code": code, "message": message},
    )
