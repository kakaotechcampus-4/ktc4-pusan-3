"""tool 실행 결과의 공통 반환 규격.

네 도메인 Agent 와 Memory 가 같은 봉투를 쓴다. 어휘가 Agent 마다 다르면 같은 실패가
로그에서 다른 이름으로 남아 집계가 안 된다 (docs/agents/shared/Tool_공통.md §1).

`execute_tool` 은 여기 두지 않는다. 허용 목록·핸들러 표·컨텍스트 타입이 Agent 마다 달라
공통화하면 타입만 깊어지고 얻는 게 없다. 각 Agent 의 `registry.py` 가 자기 것을 갖는다.
"""

from dataclasses import dataclass, field
from typing import Any


class ErrorCode:
    """tool 이 돌려줄 수 있는 실패 사유. 각 코드는 모델이 할 다음 행동과 1:1 이다.

    Agent 가 자기 코드를 더할 때는 이 클래스를 상속한다. 값을 덮어쓰지 않는다.
    """

    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"  # 이번 요청에서 열리지 않은 tool → 열린 것 중에서 고름
    INVALID_ARGS = "INVALID_ARGS"  # 인자가 스키마와 안 맞음 → 고쳐서 다시 호출
    NOT_FOUND = "NOT_FOUND"  # 대상이 없음 → 사용자에게 확인
    NO_RECORDS = "NO_RECORDS"  # 근거 기록 0건 → 일반 추천으로 가거나 되물음
    UPSTREAM_ERROR = "UPSTREAM_ERROR"  # 외부 조회 실패 → 빈 목록으로 숨기지 않는다
    CONSENT_REQUIRED = "CONSENT_REQUIRED"  # child_health 동의 없음 → 모델을 부르지 않는다
    SAFETY_UNAVAILABLE = "SAFETY_UNAVAILABLE"  # health_safety 조회 실패 → 추천 중단
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"  # 근거 id 가 이번 run 조회 결과 밖 → 후보 거절


@dataclass(frozen=True)
class ToolResult:
    """모델에게 돌려줄 tool 실행 결과.

    operation 은 Agent 마다 값 집합이 달라 str 로 둔다 (Memory 는 parse/create/update/delete,
    Food 는 query/propose/report). 좁히는 것은 각 Agent 의 ok()/fail() 래퍼가 한다.
    """

    success: bool
    operation: str | None  # None = 실행 전에 거절됨 (없는 tool, 인자 오류)
    resource: str  # tool 이름 또는 대상 테이블
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


def ok(operation: str | None, resource: str, **data: Any) -> ToolResult:
    return ToolResult(success=True, operation=operation, resource=resource, data=data)


def fail(operation: str | None, resource: str, code: str, message: str) -> ToolResult:
    """message 는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.

    발화 원문·음식명·증상·약명을 message 에 넣지 않는다. 로그로 흘러간다.
    """
    return ToolResult(
        success=False,
        operation=operation,
        resource=resource,
        error={"code": code, "message": message},
    )
