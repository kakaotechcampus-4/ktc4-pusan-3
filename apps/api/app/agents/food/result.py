"""Food tool 실행 결과의 공통 반환 규격."""

from dataclasses import dataclass, field
from typing import Any, Literal

# query = 조회, propose = 식단 후보 제출, report = 영양소 분석 보고
Operation = Literal["query", "propose", "report"]


class ErrorCode:
    """Food tool 이 돌려줄 수 있는 실패 사유. 각 코드는 모델이 할 다음 행동과 1:1 이다."""

    VALIDATION_ERROR = "VALIDATION_ERROR"  # 인자를 고쳐 다시 호출
    UNKNOWN_TOOL = "UNKNOWN_TOOL"  # 없거나 이번 task 에서 열리지 않은 tool
    NO_RECORDS = "NO_RECORDS"  # 근거 기록이 없으면 일반 추천으로 가거나 재질문


@dataclass(frozen=True)
class ToolResult:
    success: bool
    operation: Operation | None  # None = 실행 전에 거절됨 (없는 tool, 인자 오류)
    resource: str  # tool 이름
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


def fail(operation: Operation | None, resource: str, code: str, message: str) -> ToolResult:
    # message는 모델이 읽고 무엇을 해야 하는지까지 적는다
    return ToolResult(
        success=False,
        operation=operation,
        resource=resource,
        error={"code": code, "message": message},
    )
