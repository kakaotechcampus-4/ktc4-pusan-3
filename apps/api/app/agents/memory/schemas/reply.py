"""Memory가 보호자에게 하는 마지막 말의 모양. structured output 으로 받는다.

tool을 부르지 않은 턴의 content가 이 스키마의 JSON 이다. 말은 그 턴에만 나오므로 모델은
언제나 모든 tool 결과를 본 뒤에 말한다.

되묻기와 일반 안내를 kind로 가른다.
"""

from typing import Annotated, Any, Literal

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict, Field

ReplyKind = Literal["question", "message"]


class ReplyOutput(BaseModel):
    # strict 모드는 모든 필드가 required 여야 한다. pending_hint 는 기본값 없이 null을 허용한다
    model_config = ConfigDict(extra="forbid")

    text: Annotated[
        str,
        Field(description="보호자가 읽을 한국어 문장. 마크다운을 쓰지 않는다"),
    ]
    kind: Annotated[
        ReplyKind,
        Field(
            description=(
                "question: 보호자의 답을 받아야 이어서 처리할 수 있다. "
                "message: 답이 필요 없는 안내나 조회 결과다"
            )
        ),
    ]
    pending_hint: Annotated[
        str | None,
        Field(
            description=(
                "kind=question 일 때, 아직 저장하지 못한 기록 후보 한 줄을 그대로 적는다. "
                "후보 목록에 있는 문장이어야 하고 새로 쓰지 않는다. 그 밖에는 null"
            ),
        ),
    ]


REPLY_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "MemoryReply",
        "schema": to_strict_json_schema(ReplyOutput),
        "strict": True,
    },
}


class ContinuationReplyOutput(ReplyOutput):
    """이어받기 run의 마지막 말. 답에 다른 말이 섞여 왔는지를 더 받는다."""

    leftover: Annotated[
        bool,
        Field(
            description=(
                "보호자의 답에 이어서 처리할 조각과 상관없는 기록이나 요청이 "
                "섞여 있었으면 true. 그 부분은 저장하지도 답하지도 않는다. "
                "따로 보내 달라는 말은 text 에 쓰지 않는다"
            )
        ),
    ]


CONTINUATION_REPLY_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "MemoryContinuationReply",
        "schema": to_strict_json_schema(ContinuationReplyOutput),
        "strict": True,
    },
}
