"""run 채널 → SSE 바이트.

프레임 규칙은 프론트 파서(apps/web/src/lib/api/sse.ts `parseFrame`)와 맞춘다 —
`event:` 줄, `data:` JSON 한 줄, 빈 줄 구분자. `data:` 없는 프레임은 파서가 버린다.

🚨 하트비트는 `:` 주석이 아니라 `event: ping` 이다. 화면의 20초 무응답 타이머
   (hooks/use-run-stream.ts `RUN_IDLE_TIMEOUT_MS`)는 파싱된 이벤트만 되살린다.
   Memory Agent 의 모델 호출이 20초를 넘으면(추론 모델은 넘는다) ping 이 없으면 화면이
   실패로 떨어진다.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from app.agents.entrypoint import failure_message
from app.api.runs.registry import RunChannel

SseEvent = tuple[str, dict]
"""채널에 넣는 모양 — (이름, 내용). frame() 이 이걸 `event:` · `data:` 두 줄로 쓴다."""


def frame(name: str, payload: dict) -> str:
    """이벤트 하나를 SSE 프레임 문자열로. 한글은 그대로, JSON 은 공백 없이 한 줄."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


def failed_event(reason: str, raw_text: str) -> SseEvent:
    """화면용 failed. 모양을 한 곳에서 정한다 — 번역기(Agent 의 Failed)와 러너(timeout ·
    internal_error)가 같이 쓴다.

    raw_text 가 있어야 "적어주신 말은 입력창에 그대로 남겨뒀어요" 가 성립한다 (계약서 §06).
    message 는 이유마다 코드가 정한 문구다 (#768) — 화면이 그대로 그리고 다시 쓰지 않는다.
    """
    return "failed", {"reason": reason, "raw_text": raw_text, "message": failure_message(reason)}


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


async def stream_remote(
    run_id: str, *, poll_seconds: float = 0.25, heartbeat_seconds: float = 10.0
) -> AsyncIterator[str]:
    """다른 워커가 돌리는 run 을 표(`run_event`)에서 읽어 프레임으로 내준다 (#877 4단계).

    `stream` 과 같은 약속 — 처음부터, 조용하면 ping, 닫히면 끝. 새 이벤트는 `poll_seconds` 마다
    본다(같은 워커보다 최대 그만큼 늦다).
    🚨 채널 행을 이벤트보다 **먼저** 읽는다. 닫힘은 마지막 이벤트와 같거나 뒤의 commit 에
       쓰이므로, 닫힌 것을 본 뒤 읽은 이벤트가 마지막까지다. 순서를 바꾸면 그 사이 쓰인 마지막
       이벤트를 놓친다.
    """
    from app.api.runs import registry
    from app.domains.run_channel.repository import events_from, get_channel

    seq = 0
    quiet = 0.0
    while True:
        async with registry._session_factory() as session:
            header = await get_channel(session, run_id=run_id)
            rows = await events_from(session, run_id=run_id, seq=seq)
        for row in rows:
            seq = row.seq + 1
            yield frame(row.name, row.payload)
        if rows:
            quiet = 0.0
            continue
        if header is None or header.closed:
            return
        await asyncio.sleep(poll_seconds)
        quiet += poll_seconds
        if quiet >= heartbeat_seconds:
            quiet = 0.0
            yield frame("ping", {})
