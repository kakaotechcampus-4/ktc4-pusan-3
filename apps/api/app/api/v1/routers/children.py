"""아이 스코프 엔드포인트 — 계약서 §06. 지금은 `POST /children/{cid}/inputs` 하나.

인증은 여기서 하지 않는다. router.py 의 protected_router 에 붙어서 자동으로 걸린다.

🚨 3단계 — 아이 소유 확인(403)은 아직 없다 (#134 9단계). cid 는 아무 UUID 나 받는다.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.api import idempotency, quota
from app.api.deps.auth import CurrentParent
from app.api.errors import ApiError
from app.api.runs import pending_reply, registry, runner
from app.api.v1.schemas.children import CreateInputRequest, CreateInputResponse
from app.core.config import settings

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

    순서가 계약이다: 키 확인 → 재생 → 이어받기 맥락 → 채널 열기 → **키 기억** → 러너 시작.
    키를 러너보다 먼저 적어 두면 같은 키가 몇 ms 뒤에 또 와도 새 run 이 아니라 재생으로
    흡수된다 (idempotency.py). 이어받기 맥락은 재생 뒤에 꺼낸다 — 같은 키 재생은 이미 꺼낸
    맥락으로 돈 run 을 돌려받아야 하고, 400 을 받으면 안 된다.
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

    continuation = None
    if body.reply_to is not None:
        continuation = pending_reply.consume(
            run_id=body.reply_to, parent_id=parent.parent_id, child_id=cid
        )
        if continuation is None:
            # 없는 run · 남의 run · 다른 아이 · 이미 답한 질문 · 만료를 하나로 합친다.
            # 이유를 갈라 알려주면 그 run_id 가 있는지 없는지가 드러난다.
            raise ApiError(
                400,
                "reply_context_unavailable",
                "이전 질문을 이어서 확인할 수 없어요. 내용을 한 번만 다시 적어 주세요.",
            )

    # 재생 뒤에 센다 — 같은 키 재생은 새 입력이 아니다. Agent 를 부르기 전에 막는다 (quota.py).
    if not quota.consume(
        parent_id=parent.parent_id, today=quota.today_kst(), limit=settings.INPUT_DAILY_LIMIT
    ):
        raise ApiError(429, "daily_input_limit", "오늘은 더 적을 수 없어요. 내일 다시 적어 주세요.")

    channel = registry.open_run(parent_id=parent.parent_id)
    idempotency.remember(**scope, run_id=channel.run_id)
    # 보호자는 본문이 아니라 토큰에서 — Memory 가 작성자로 적어서 보호자의 말이 아이의 사실이
    # 되지 않는다 (§2).
    job = runner.agent_job(
        child_id=cid,
        parent_id=parent.parent_id,
        raw_text=body.text,
        continuation=continuation,
        reply_to=body.reply_to,
    )
    runner.start(channel, job, raw_text=body.text)
    return CreateInputResponse(run_id=channel.run_id)
