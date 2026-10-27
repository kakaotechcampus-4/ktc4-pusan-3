"""읽기 전용 출구(인자 없는 출력 tool)를 모델이 잘못 부르는 길을 코드로 막는다.

`show_dose_status` · `defer_dose_question` · `show_fever_timeline` 은 표에 안 걸린 질문이 가는
출구다. 항상 성공하고, 성공하면 loop 가 끝난다. 그런데 `run_tool_loop` 는 한 응답 안의 tool 을
온 순서대로 실행하면서 출력 tool 이 하나라도 통과하면 그 스텝에서 끝낸다. 출구가 같은 응답의 쓰기
출력 tool(등록 · 수정 · 중단)이나 정리와 같이 오면, 인자 오류로 실패한 쓰기 요청이 고쳐 볼 기회
없이 사라지고 보호자는 묻지도 않은 안내를 받는다. 프롬프트에 "같이 부르지 않는다" 고 적어 두는
것만으로는 막히지 않아서, 같은 응답에 다른 출력 tool 이 있으면 출구를 거절한다. 거절된 출구는
통과하지 않으니 쓰기 요청이 실패해도 다음 왕복에서 고쳐 낼 수 있다. 출구끼리는 같이 불러도 된다.
"""

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from app.agents.health.result import ErrorCode, ToolResult, fail

READ_ONLY_EXITS: frozenset[str] = frozenset(
    {"show_dose_status", "defer_dose_question", "show_fever_timeline"}
)


def same_response_calls(messages: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """모델의 가장 최근 응답에 든 tool 이름.

    `run_tool_loop` 는 tool 을 실행하기 전에 그 응답을 `messages` 에 먼저 붙인다. 앞 왕복의 호출은
    세지 않는다 — 쓰기가 앞 왕복에서 끝났으면 지금 출구는 막을 이유가 없다.
    """
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return tuple(call["function"]["name"] for call in message.get("tool_calls") or ())
    return ()


def blocked_exit(
    name: str, messages: Sequence[Mapping[str, Any]], *, output_tools: Collection[str]
) -> ToolResult | None:
    """같은 응답에 다른 출력 tool 이 있어 이 출구를 거절해야 하면 그 결과를, 아니면 None 을 낸다."""
    if name not in READ_ONLY_EXITS:
        return None
    others = [
        other
        for other in same_response_calls(messages)
        if other in output_tools and other not in READ_ONLY_EXITS
    ]
    if not others:
        return None
    return fail(
        None,
        name,
        ErrorCode.EXIT_REJECTED,
        "이 tool 은 다른 출력 tool 과 같은 응답에서 부르지 않는다. 다른 출력 tool 의 요청부터 "
        "처리한다. 그 결과를 받은 뒤에도 이 안내가 필요하면 이 tool 만 따로 부른다.",
    )
