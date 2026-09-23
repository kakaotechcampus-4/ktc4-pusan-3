"""SSE 프레임 — app/api/runs/sse.py

프론트 파서(apps/web/src/lib/api/sse.ts `parseFrame`)의 규칙을 그대로 고정한다.
- 프레임 = `event: <이름>` 줄 + `data: <JSON 한 줄>` 줄 + 빈 줄.
  빈 줄이 구분자라 마지막에도 있어야 한다.
- `data:` 가 없는 프레임은 버린다. 그래서 `:` 주석은 하트비트로 못 쓴다 —
  화면의 20초 무응답 타이머(hooks/use-run-stream.ts `RUN_IDLE_TIMEOUT_MS`)는
  파싱된 이벤트만 되살린다.

이 파일은 HTTP 없이 문자열과 async 제너레이터만 본다.
"""

import asyncio

import pytest

from app.api.runs import registry, sse


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def test_frame_is_event_line_data_line_and_blank_separator():
    result = sse.frame("step", {"index": 1, "total": 3})

    assert result == 'event: step\ndata: {"index":1,"total":3}\n\n'


def test_frame_keeps_korean_readable_and_compact():
    """`ensure_ascii=False` 와 구분자 없는 JSON 을 고정한다.

    한글이 \\uXXXX 로 나가면 curl 로 볼 때 읽을 수가 없다.
    """
    assert sse.frame("step", {"label": "기록 중"}) == 'event: step\ndata: {"label":"기록 중"}\n\n'


def test_frame_data_stays_on_one_line_even_if_value_has_newline():
    """값 안의 개행은 JSON 이 \\n 으로 이스케이프한다.

    개행 문자가 그대로 섞이면 파서가 프레임을 둘로 자른다.
    """
    result = sse.frame("failed", {"reason": "첫 줄\n둘째 줄"})

    assert result.count("\n") == 3  # event 줄 끝 · data 줄 끝 · 빈 줄
    assert "\\n" in result


async def test_stream_of_closed_channel_is_exactly_its_frames():
    channel = registry.open_run()
    channel.publish(("step", {"index": 1}))
    channel.publish(("done", {"run_id": channel.run_id}))
    channel.close()

    frames = [f async for f in sse.stream(channel)]

    assert frames == [
        'event: step\ndata: {"index":1}\n\n',
        f'event: done\ndata: {{"run_id":"{channel.run_id}"}}\n\n',
    ]


async def test_stream_sends_ping_while_channel_is_quiet_then_ends_on_close():
    """🚨 Memory Agent 의 모델 호출이 20초를 넘으면 화면이 실패로 떨어진다.

    그래서 조용할 때 ping 을 보낸다. heartbeat 를 0.01초로 줄여 "조용한 동안" 을
    테스트 안에서 만든다.
    """
    channel = registry.open_run()
    frames: list[str] = []

    async def consume():
        async for f in sse.stream(channel, heartbeat_seconds=0.01):
            frames.append(f)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # 아무것도 안 넣고 기다린다 — ping 이 몇 번 나가야 한다
    channel.publish(("done", {}))
    channel.close()
    await asyncio.wait_for(task, timeout=1)

    assert "event: ping\ndata: {}\n\n" in frames
    assert frames[-1] == "event: done\ndata: {}\n\n"
