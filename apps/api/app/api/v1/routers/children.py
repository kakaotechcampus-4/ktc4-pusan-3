"""아이 스코프 엔드포인트 — 계약서 §06. 지금은 `POST /children/{cid}/inputs` 하나.

인증은 여기서 하지 않는다. router.py 의 protected_router 에 붙어서 자동으로 걸린다.

🚨 3단계 — 아이 소유 확인(403)은 아직 없다 (#134 9단계). cid 는 아무 UUID 나 받는다.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.api import idempotency
from app.api.deps.auth import CurrentParent
from app.api.errors import ApiError
from app.api.runs import registry, runner
from app.api.v1.schemas.children import CreateInputRequest, CreateInputResponse

router = APIRouter()


@router.post("/children/{cid}/inputs", status_code=202)
async def create_input(
    cid: UUID,
    body: CreateInputRequest,
    parent: CurrentParent,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> CreateInputResponse:
    """접수만 한다 — 채널을 열고 러너를 뒤에서 띄우고 바로 202. Agent 를 기다리지 않는다.

    순서가 계약이다: 키 확인 → 재생 → 채널 열기 → **키 기억** → 러너 시작. 키를 러너보다 먼저
    적어 두면 같은 키가 몇 ms 뒤에 또 와도 새 run 이 아니라 재생으로 흡수된다 (idempotency.py).
    """
    if not idempotency_key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key 헤더가 필요해요")

    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if replayed is not None:
        return CreateInputResponse(run_id=replayed)

    channel = registry.open_run(parent_id=parent.parent_id)
    idempotency.remember(**scope, run_id=channel.run_id)
    runner.start(channel, runner.fake_job, raw_text=body.text)
    return CreateInputResponse(run_id=channel.run_id)
