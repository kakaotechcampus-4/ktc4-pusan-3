"""run 진행 이벤트 — 계약서 §06 `GET /runs/{rid}/events`.

인증은 여기서 하지 않는다. router.py 의 protected_router 에 붙어서 자동으로 걸린다.
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps.auth import CurrentParent
from app.api.errors import ApiError
from app.api.runs import registry, sse

router = APIRouter()


@router.get("/runs/{rid}/events")
async def stream_run_events(rid: str, parent: CurrentParent) -> StreamingResponse:
    """채널에 쌓인 이벤트를 처음부터 SSE 로 흘린다. 늦게 붙어도, 새로고침해도 처음부터다.

    🚨 응답을 한 덩어리로 만들지 않는다. async 제너레이터를 그대로 넘기면 FastAPI 가
       프레임이 나올 때마다 소켓에 쓴다 — 그래서 연결이 run 이 끝날 때까지 열려 있다.
    🚨 헤더 두 줄은 중간 서버용이다. nginx 는 기본으로 응답을 모았다가 보내서, 없으면 진행 단계가
       화면에 안 흐르고 20초 뒤 한꺼번에 도착한다(그땐 이미 "결과를 받지 못했어요").
    """
    channel = registry.get(rid)
    # 🚨 남의 run 은 없는 run 과 똑같이 404 다. 403 이면 "그 run_id 가 있다" 는 것부터 샌다.
    #    failed 에는 보호자가 적은 문장이 그대로 실려 있다.
    if channel is None or channel.parent_id != parent.parent_id:
        raise ApiError(404, "not_found", "진행 중인 처리를 찾을 수 없어요")
    return StreamingResponse(
        sse.stream(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
