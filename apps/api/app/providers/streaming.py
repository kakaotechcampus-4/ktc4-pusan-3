"""Elice MLAPI 스트리밍 응답을 한 덩어리로 모은다.

게이트웨이는 스트리밍이 아닌 요청의 출력을 2,000 토큰으로 막는다 — 답이 끝나기 전에는 보이지 않아
긴 요청을 도중에 끊기 때문이다 (2026-11-01 실측, 400 으로 거절). 긴 JSON 은 스트리밍으로만 받는다.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class StreamedReply:
    content: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_prompt_tokens: int
    # 사용량 기록(core/llm_usage)용. 게이트웨이가 실제로 쓴 모델 이름 · 추론 토큰
    model: str | None = None
    reasoning_tokens: int | None = None


def collect(stream: Iterable) -> StreamedReply:
    parts: list[str] = []
    finish_reason: str | None = None
    usage = None
    model: str | None = None
    for chunk in stream:
        # 조각마다 누적 사용량이 실려 온다 — 더하지 않고 마지막 것만 쓴다
        if chunk.usage is not None:
            usage = chunk.usage
        served = getattr(chunk, "model", None)
        model = served if isinstance(served, str) and served else model
        for choice in chunk.choices:
            if choice.delta.content:
                parts.append(choice.delta.content)
            finish_reason = choice.finish_reason or finish_reason
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    return StreamedReply(
        content="".join(parts),
        finish_reason=finish_reason,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        cached_prompt_tokens=int(getattr(usage, "cached_prompt_tokens", 0) or 0) if usage else 0,
        model=model,
        reasoning_tokens=getattr(details, "reasoning_tokens", None),
    )
