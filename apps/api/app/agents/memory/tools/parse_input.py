"""복합 발화를 의미 단위로 나누는 tool.

모델이 스스로 세운 계획을 되돌려 받아 다음 tool을 고르게 하는 단계다.
분리 자체는 LLM이 하고, 이 함수는 결과를 구조화된 형태로 정리해 돌려준다.
"""

from app.agents.memory.context import AgentContext
from app.agents.memory.result import ToolResult, ok
from app.agents.memory.schemas.parse_input import ParseInputArgs, SegmentIntent

RESOURCE = "input"

# 이 intent들은 저장 및 조회 tool로 이어지지 않는다. 모델이 남은 작업 수를 착각하지 않게 세어 준다
_TERMINAL_INTENTS = {SegmentIntent.OUT_OF_SCOPE, SegmentIntent.UNCLEAR}


async def parse_input(context: AgentContext, args: ParseInputArgs) -> ToolResult:
    segments = [{"text": segment.text, "intent": segment.intent} for segment in args.segments]
    actionable = [segment for segment in args.segments if segment.intent not in _TERMINAL_INTENTS]
    return ok(
        "parse",
        RESOURCE,
        segments=segments,
        actionable_count=len(actionable),  # 앞으로 호출할 CRUD tool 수
        out_of_scope_count=_count(args, SegmentIntent.OUT_OF_SCOPE),
        unclear_count=_count(args, SegmentIntent.UNCLEAR),
    )


def _count(args: ParseInputArgs, intent: SegmentIntent) -> int:
    return sum(1 for segment in args.segments if segment.intent == intent)
