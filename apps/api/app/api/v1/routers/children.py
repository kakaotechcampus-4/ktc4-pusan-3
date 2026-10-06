"""아이 엔드포인트 — 계약서 §04 · §06. `POST /children`(등록) · `POST /children/{cid}/inputs`.

인증은 여기서 하지 않는다. router.py 의 protected_router 에 붙어서 자동으로 걸린다.

아이 주소(`/children/{cid}/*`)는 `AccessibleChild` 가 본문보다 먼저 확인한다 — 연결된
보호자가 아니면 403 `child_access_denied` (#134 9단계, deps/child.py). 본문은 경로의 cid 대신
확인된 id 를 쓴다.
"""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from sqlalchemy.exc import IntegrityError

from app.api import idempotency, quota
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope, constraint_name
from app.api.quota import today_kst
from app.api.runs import pending_reply, registry, runner
from app.api.v1.schemas.children import (
    CreateChildRequest,
    CreateChildResponse,
    CreateInputRequest,
    CreateInputResponse,
)
from app.core.config import settings
from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child, has_child_link
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import REQUIRED_CHILD_SCOPES, record_consent
from app.domains.policy.repository import find_active_versions
from app.rules.age import age_display

router = APIRouter()


@router.post(
    "/children",
    status_code=201,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 409)},
)
async def register_child(
    body: CreateChildRequest,
    parent: CurrentParent,
    session: SessionDep,
) -> CreateChildResponse:
    """아이를 등록한다 — 아이 · 연결(owner) · 아이 동의 2건을 한 트랜잭션에 (#92).

    🚨 동의 · 법정대리인 확인 · 약관 버전 · "이미 아이가 있음" 을 전부 아이를 만들기 전에 본다.
       하나라도 막히면 아무것도 남지 않는다 — 동의 없는 아이 행이 남는 것이 막으려는 상태다.
       consent 의 CHECK 가 아이 동의에 child_id 를 요구해서, 동의는 아이를 만든 뒤 같은
       트랜잭션에서 기록한다 (#92 결정 1번).
    오류 (순서대로): 400 validation_failed(생일) → 403 consent_required(동의 · 법정대리인) →
    400 policy_version_invalid → 409 child_already_exists. 가운데 셋은 화면 목
    (apps/web/src/mocks/handlers/children.ts)과 같은 순서이고, 앞뒤 둘을 서버가 더했다.
    """
    today = today_kst()
    # 나이 계산(age_display · life_stage)은 미래 생일에서 터지고, 너무 옛 생일은 "만 2025세" 로
    # 저장돼 Agent 에도 넘어간다. 화면 달력과 같은 범위다 (onboarding EARLIEST_BIRTH_DATE) —
    # 화면도 막지만 기기 날짜라 서버가 다시 본다.
    earliest = date(today.year - 20, 1, 1)
    if not earliest <= body.birth_date <= today:
        raise ApiError(
            400, "validation_failed", "생일을 다시 확인해 주세요", {"fields": ["birth_date"]}
        )

    granted = {consent.scope for consent in body.consents}
    missing = [scope for scope in REQUIRED_CHILD_SCOPES if scope not in granted]
    if missing:
        raise ApiError(
            403,
            "consent_required",
            "아이 정보에 대한 동의가 필요해요",
            {"scopes": [scope.value for scope in missing]},
        )
    if not body.guardian_attested:
        # 동의와 법정대리인 확인은 별개 의무다 (개인정보보호법 제22조의2 ①, #92 결정 2번)
        raise ApiError(
            403,
            "consent_required",
            "법정대리인 확인이 필요해요",
            {"scopes": [ConsentScope.CHILD_BASIC.value]},
        )

    # 가입과 같은 규칙 — GET /policies 가 지금 보여 주는 버전만 받는다 (#91 · #168)
    current = {row.scope: row for row in await find_active_versions(session, now=datetime.now(UTC))}
    versions: dict[ConsentScope, UUID] = {}
    for consent in body.consents:
        # 🚨 REQUIRED_CHILD_SCOPES 가 지금은 "아이 동의 전부" 이기도 하다. 아이 쪽에 선택 동의가
        #    생기면 이 줄이 그것을 조용히 버린다 — 그때 계정처럼 목록을 둘로 나눈다 (#172 location).
        if consent.scope not in REQUIRED_CHILD_SCOPES:
            continue
        registered = current.get(consent.scope)
        if registered is None or registered.version != consent.policy_version:
            raise ApiError(
                400,
                "policy_version_invalid",
                "동의 화면을 다시 불러와 주세요",
                {"scope": consent.scope.value, "policy_version": consent.policy_version},
            )
        versions[consent.scope] = registered.id

    # 보호자당 아이 1명. 먼저 보고, 동시에 온 두 요청은 DB 제약(uq_parent_child_parent_id)이 막는다
    if await has_child_link(session, parent_id=parent.parent_id):
        raise ApiError(409, "child_already_exists", "이미 등록한 아이가 있어요")
    try:
        child = await create_child(
            session,
            owner_parent_id=parent.parent_id,
            nickname=body.nickname,
            birth_date=body.birth_date,
            # 관계는 02 화면이 받는다 — 01 에서는 정하지 않은 것이 사실이다 (현식님 #92 댓글)
            relation=ParentChildRelation.OTHER,
        )
    except IntegrityError as exc:
        if constraint_name(exc) != "uq_parent_child_parent_id":
            raise
        await session.rollback()
        raise ApiError(409, "child_already_exists", "이미 등록한 아이가 있어요") from exc

    for scope, policy_version_id in versions.items():
        await record_consent(
            session,
            actor_parent_id=parent.parent_id,
            child_id=child.id,
            scope=scope,
            action=ConsentAction.GRANTED,
            policy_version_id=policy_version_id,
            guardian_attested=body.guardian_attested,
        )

    # 응답은 commit 전에 만든다 — commit 뒤에 터지면 저장됐는데 실패로 보인다 (#214 리뷰 ⑤)
    response = CreateChildResponse(
        id=child.id,
        nickname=child.nickname,
        age_display=age_display(child.birth_date, today),
        role="owner",
    )
    await session.commit()
    return response


@router.post("/children/{cid}/inputs", status_code=202)
async def create_input(
    child: AccessibleChild,
    body: CreateInputRequest,
    parent: CurrentParent,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> CreateInputResponse:
    """접수만 한다 — 채널을 열고 러너를 뒤에서 띄우고 바로 202. Agent 를 기다리지 않는다.

    순서: (아이 접근 확인 — 의존성) → 키 확인 → 재생 → 맥락 확인 → 한도 → 맥락 꺼내기 →
    채널 열기 → 키 기억 → 러너 시작.
    키를 러너보다 먼저 적어 두면 같은 키가 몇 ms 뒤에 또 와도 새 run이 아니라
    재생으로 흡수된다 (idempotency.py). 이어받기 맥락은 재생 뒤에 본다. 같은 키 재생은 이미 꺼낸
    맥락으로 돈 run을 돌려받아야 하고, 400 을 받으면 안 된다. 꺼내는 건 한도를 통과한 뒤다.
    """
    if not idempotency_key:
        raise ApiError(400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요")

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
        # 확인만 한다. 한도에 걸려 429 로 끝나도 맥락은 남아야 한다
        continuation = pending_reply.get(
            run_id=body.reply_to, parent_id=parent.parent_id, child_id=child.child_id
        )
        if continuation is None:
            # 없는 run · 남의 run · 다른 아이 · 이미 답한 질문 · 만료를 하나로 합친다.
            # 이유를 갈라 알려주면 그 run_id 가 있는지 없는지가 드러난다.
            raise ApiError(
                400,
                "reply_context_unavailable",
                # 무엇을 다시 보낼지는 화면이 이 문구 아래에 적는다. 앞서 저장된 이야기까지
                # 다시 적게 하면 그 조각이 두 번 저장된다
                "이전 질문을 이어서 확인할 수 없어요.",
            )

    # 재생 뒤에 센다 — 같은 키 재생은 새 입력이 아니다. Agent 를 부르기 전에 막는다 (quota.py).
    # 되묻기 답도 센다. 이어받기 run 도 Memory 를 부르고, 되묻기가 이어지는 횟수에 상한이 없다.
    if not quota.consume(
        parent_id=parent.parent_id, today=quota.today_kst(), limit=settings.INPUT_DAILY_LIMIT
    ):
        raise ApiError(429, "daily_input_limit", "오늘은 더 적을 수 없어요. 내일 다시 적어 주세요.")

    if body.reply_to is not None:
        # get 과 여기 사이에 await 가 없어서 같은 reply_to 로 온 다른 요청이 끼어들지 못한다.
        # 한 번만 쓴다. 같은 질문에 두 번 답하면 관찰이 두 행이 된다
        pending_reply.consume(
            run_id=body.reply_to, parent_id=parent.parent_id, child_id=child.child_id
        )

    channel = registry.open_run(parent_id=parent.parent_id)
    idempotency.remember(**scope, replay=channel.run_id)
    # 보호자는 본문이 아니라 토큰에서 — Memory 가 작성자로 적어서 보호자의 말이 아이의 사실이
    # 되지 않는다 (§2).
    job = runner.agent_job(
        child_id=child.child_id,
        birth_date=child.birth_date,
        parent_id=parent.parent_id,
        raw_text=body.text,
        continuation=continuation,
        reply_to=body.reply_to,
    )
    runner.start(channel, job, raw_text=body.text)
    return CreateInputResponse(run_id=channel.run_id)
