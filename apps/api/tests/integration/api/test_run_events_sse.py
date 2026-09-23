"""`GET /runs/{rid}/events` — 계약서 §06 · SSE.

run 채널(app/api/runs/registry.py)에 쌓인 이벤트를 SSE 로 흘린다.

🚨 여기서는 **미리 닫아 둔 채널**만 쓴다. 살아 있는 채널을 요청하면 응답 본문이 끝나지 않아
   테스트 클라이언트가 영원히 기다린다. 살아 있는 채널의 동작(ping · close 로 끝남)은
   tests/unit/api/test_sse_frame.py 가 HTTP 없이 본다.
"""

from app.api.runs import registry

EVENTS = "/api/v1/runs/{rid}/events"


async def test_events_requires_authentication(db_client):
    """🚨 무인증 예외는 auth 5개뿐이다 (루트 CLAUDE.md §9). run 도 보호자 것이다."""
    response = await db_client.get(EVENTS.format(rid="anything"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_unknown_run_is_404(db_client, bearer):
    headers, _ = bearer

    response = await db_client.get(EVENTS.format(rid="no-such-run"), headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_closed_channel_streams_exact_bytes(db_client, bearer):
    """프론트 파서가 읽는 그대로.

    프레임마다 빈 줄, 마지막에도 빈 줄, 한글은 그대로, data 는 한 줄.
    """
    headers, _ = bearer
    channel = registry.open_run()
    channel.publish(("step", {"index": 1, "total": 3, "label": "기록 중"}))
    channel.publish(("done", {"run_id": channel.run_id, "model_calls": 0}))
    channel.close()

    response = await db_client.get(EVENTS.format(rid=channel.run_id), headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == (
        'event: step\ndata: {"index":1,"total":3,"label":"기록 중"}\n\n'
        f'event: done\ndata: {{"run_id":"{channel.run_id}","model_calls":0}}\n\n'
    )
