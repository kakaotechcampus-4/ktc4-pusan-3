"""Growth tool 실행 결과.

봉투(`ToolResult`)와 실패 코드는 `common/tool_runtime.py` 가 정본이다. Growth 전용 코드는 공통
`ErrorCode` 를 상속해 얹는다 (Activity `result.py` 와 같은 모양 — 패키지끼리
import 하지 않아 사본이다).
"""

from typing import Any, Literal

from app.agents.common.tool_runtime import ErrorCode as CommonErrorCode
from app.agents.common.tool_runtime import ToolResult
from app.agents.common.tool_runtime import fail as _fail
from app.agents.common.tool_runtime import ok as _ok

# query = 조회, propose = 후보 제출
Operation = Literal["query", "propose"]

__all__ = ["ErrorCode", "Operation", "ToolResult", "fail", "ok"]


class ErrorCode(CommonErrorCode):
    # 출력 검증에서 후보가 거절됨 → 사유대로 고쳐 다시 제출한다.
    # 안전 필터 · 금지 표현에 걸린 후보는 이 코드가 아니다 — 빠지고 사유도 알려 주지 않는다
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    # 이번 run 의 도서 검색어를 다 썼다 → 찾은 책 중에서 고르거나 빈 목록으로 제출한다
    SEARCH_LIMIT = "SEARCH_LIMIT"


def ok(operation: Operation, resource: str, **data: Any) -> ToolResult:
    return _ok(operation, resource, **data)


def fail(operation: Operation | None, resource: str, code: str, message: str) -> ToolResult:
    # message 는 모델이 읽는다. 무엇을 해야 하는지까지 적는다.
    # 발화 원문 · 활동명 · 알레르기 이름을 넣지 않는다 — 로그로 흘러간다
    return _fail(operation, resource, code, message)
