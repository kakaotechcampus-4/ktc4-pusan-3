"""run 채널 → SSE 바이트.

프레임 규칙은 프론트 파서(apps/web/src/lib/api/sse.ts `parseFrame`)와 맞춘다 —
`event:` 줄, `data:` JSON 한 줄, 빈 줄 구분자. `data:` 없는 프레임은 파서가 버린다.

🚨 하트비트는 `:` 주석이 아니라 `event: ping` 이다. 화면의 20초 무응답 타이머
   (hooks/use-run-stream.ts `RUN_IDLE_TIMEOUT_MS`)는 파싱된 이벤트만 되살린다.
   Memory Agent 의 모델 호출이 20초를 넘으면(추론 모델은 넘는다) ping 이 없으면 화면이
   실패로 떨어진다.
"""

import json
from collections.abc import AsyncIterator

from app.api.runs.registry import RunChannel


def frame(name: str, payload: dict) -> str:
    """이벤트 하나를 SSE 프레임 문자열로. 한글은 그대로, JSON 은 공백 없이 한 줄."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


async def stream(channel: RunChannel, *, heartbeat_seconds: float = 10.0) -> AsyncIterator[str]:
    """채널의 이벤트를 처음부터 프레임으로 내주고, 조용할 땐 ping 을 낸다. 채널이 닫히면 끝.

    registry.subscribe 와 같은 모양에 "자다가 시간이 지나면 ping" 한 갈래만 더 있다.
    채널에 든 이벤트는 (이름, payload dict) 한 쌍이다 — 러너가 그 모양으로 넣는다.
    """
    i = 0
    while True:
        if i >= len(channel.events):
            if channel.closed:
                return
            if not await channel.wait_changed(heartbeat_seconds):
                yield frame("ping", {})
            continue
        name, payload = channel.events[i]
        i += 1
        yield frame(name, payload)
