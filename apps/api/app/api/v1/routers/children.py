"""아이 엔드포인트 — 계약서 §04 · §05 · §06.

`POST /children`(등록) · `GET` · `PATCH /children/{cid}`(프로필) ·
`POST /children/{cid}/onboarding`(02 화면) · `/children/{cid}/growth`(키·몸무게 기록) ·
`POST /children/{cid}/inputs`.

인증은 여기서 하지 않는다. router.py 의 protected_router 에 붙어서 자동으로 걸린다.

아이 주소(`/children/{cid}/*`)는 `AccessibleChild` 가 본문보다 먼저 확인한다 — 연결된
보호자가 아니면 403 `child_access_denied` (#134 9단계, deps/child.py). 본문은 경로의 cid 대신
확인된 id 를 쓴다.
"""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.agents import entrypoint
from app.api import idempotency, quota
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope, constraint_name
from app.api.quota import today_kst
from app.api.runs import chat_record, pending_reply, registry, runner
from app.api.v1.schemas.children import (
    ChildProfile,
    CreateChildRequest,
    CreateChildResponse,
    CreateGrowthLogRequest,
    CreateInputRequest,
    CreateInputResponse,
    GrowthLogOut,
    GrowthLogResponse,
    GrowthLogsResponse,
    OnboardingRequest,
    OnboardingResponse,
    UpdateChildRequest,
    UpdateChildResponse,
    UpdateGrowthLogRequest,
)
from app.core.config import settings
from app.domains.chat.repository import find_turn_date
from app.domains.child.models import Child, ChildGrowthLog, ParentChildRelation
from app.domains.child.repository import (
    create_child,
    create_growth_log,
    delete_growth_log,
    find_child_link,
    find_growth_log,
    has_child_link,
    list_growth_logs,
    set_relation,
    update_child,
)
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import (
    REQUIRED_CHILD_SCOPES,
    child_health_granted,
    location_granted,
    record_consent,
)
from app.domains.policy.repository import find_active_versions
from app.rules.age import age_display
from app.rules.observed_label import observed_label

router = APIRouter()


def _check_birth_date(birth_date: date, today: date) -> None:
    """화면 달력과 같은 범위 — 올해 - 20년의 1월 1일 ~ 오늘 (한국 날짜). 아니면 400.

    나이 계산(age_display · life_stage)은 미래 생일에서 터지고, 너무 옛 생일은 "만 2025세" 로
    저장돼 Agent 에도 넘어간다. 화면도 막지만 기기 날짜라 서버가 다시 본다
    (onboarding EARLIEST_BIRTH_DATE). 등록과 고치기가 같은 범위를 쓴다.
    """
    if not date(today.year - 20, 1, 1) <= birth_date <= today:
        raise ApiError(
            400, "validation_failed", "생일을 다시 확인해 주세요", {"fields": ["birth_date"]}
        )


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
    _check_birth_date(body.birth_date, today)

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


def _profile(
    child: Child, relation: ParentChildRelation, *, viewer_id: UUID, today: date
) -> ChildProfile:
    return ChildProfile(
        id=child.id,
        nickname=child.nickname,
        birth_date=child.birth_date,
        age_display=age_display(child.birth_date, today),
        gender=child.gender,
        relation=relation,
        role="owner" if child.owner_parent_id == viewer_id else "member",
    )


def _growth_out(row: ChildGrowthLog, today: date) -> GrowthLogOut:
    return GrowthLogOut(
        id=row.id,
        measured_on=row.measured_on,
        height_cm=None if row.height_cm is None else float(row.height_cm),
        weight_kg=None if row.weight_kg is None else float(row.weight_kg),
        measured_label=observed_label(row.measured_on, today=today),
        note=row.note,
    )


def _check_measured_on(measured_on: date, *, birth_date: date, today: date) -> None:
    """잰 날은 아이 생일부터 오늘(한국 날짜)까지. 아니면 400."""
    if not birth_date <= measured_on <= today:
        raise ApiError(
            400, "validation_failed", "잰 날을 다시 확인해 주세요", {"fields": ["measured_on"]}
        )


@router.get("/children/{cid}", responses={403: {"model": ErrorEnvelope}})
async def read_child(
    child: AccessibleChild, parent: CurrentParent, session: SessionDep
) -> ChildProfile:
    """아이 프로필. 관계 · 역할은 보는 보호자 기준이다 — 같은 아이도 보호자마다 다르게 받는다."""
    link = await find_child_link(session, child_id=child.child_id, parent_id=parent.parent_id)
    if link is None:
        # AccessibleChild 가 방금 확인했다. 그 사이에 연결이 끊긴 경우만 여기 온다
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    row, relation = link
    return _profile(row, relation, viewer_id=parent.parent_id, today=today_kst())


@router.patch(
    "/children/{cid}", responses={status: {"model": ErrorEnvelope} for status in (400, 403)}
)
async def update_child_profile(
    child: AccessibleChild, body: UpdateChildRequest, parent: CurrentParent, session: SessionDep
) -> UpdateChildResponse:
    """아이 프로필 고치기 — 보낸 칸만 바꾼다. 연결된 보호자 누구나 고친다 (역할 제한 없음).

    승인 게이트가 아니다(루트 §2 — 되돌릴 수 있는 수정). 그래서 Idempotency-Key 를 받지 않는다.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "validation_failed", "고칠 내용이 없어요", {"fields": []})
    today = today_kst()
    if "birth_date" in changes:
        _check_birth_date(changes["birth_date"], today)

    updated = await update_child(
        session, child_id=child.child_id, parent_id=parent.parent_id, **changes
    )
    link = await find_child_link(session, child_id=child.child_id, parent_id=parent.parent_id)
    if updated is None or link is None:
        # AccessibleChild 가 방금 확인했다. 그 사이에 연결이 끊긴 경우만 여기 온다
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    row, relation = link
    # 응답은 commit 전에 만든다 — commit 뒤에 터지면 저장됐는데 실패로 보인다 (#214 리뷰 ⑤)
    response = UpdateChildResponse(
        child=_profile(row, relation, viewer_id=parent.parent_id, today=today)
    )
    await session.commit()
    return response


@router.post(
    "/children/{cid}/onboarding",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def submit_onboarding(
    child: AccessibleChild,
    body: OnboardingRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> OnboardingResponse:
    """아이 등록 뒤 02 화면 — 관계 · 성별 · 첫 키·몸무게를 한 트랜잭션에 저장한다.

    관계는 보내는 보호자 자신의 것이다. 키 · 몸무게를 하나라도 적었으면 오늘(한국 날짜) 잰 기록
    한 줄을 만든다. 같은 키로 다시 오면 처음 응답을 그대로 돌려준다 (일정 제출과 같은 방식).
    순서: (본문 형식 · 아이 접근 — 의존성) → 키 확인 → 재생 → 빈 본문 → 저장 → commit → 응답 기억.
    본문 형식 검사가 키 확인보다 먼저라, 키 없이 틀린 본문이 오면 validation_failed 다.
    """
    if not idempotency_key:
        raise ApiError(
            400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
        )
    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if isinstance(replayed, dict):
        return JSONResponse(status_code=200, content=replayed)

    if not body.model_fields_set:
        raise ApiError(400, "validation_failed", "저장할 내용이 없어요", {"fields": []})

    today = today_kst()
    if body.relation is not None:
        await set_relation(
            session, child_id=child.child_id, parent_id=parent.parent_id, relation=body.relation
        )
    if body.gender is not None:
        await update_child(
            session, child_id=child.child_id, parent_id=parent.parent_id, gender=body.gender
        )
    growth = None
    if body.height_cm is not None or body.weight_kg is not None:
        growth = await create_growth_log(
            session,
            child_id=child.child_id,
            created_by=parent.parent_id,
            measured_on=today,
            height_cm=body.height_cm,
            weight_kg=body.weight_kg,
        )

    link = await find_child_link(session, child_id=child.child_id, parent_id=parent.parent_id)
    if link is None:
        # AccessibleChild 가 방금 확인했다. 그 사이에 연결이 끊긴 경우만 여기 온다
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    row, relation = link
    # 응답은 commit 전에 만든다 — commit 뒤에 터지면 저장됐는데 실패로 보인다 (#214 리뷰 ⑤)
    response = OnboardingResponse(
        child=_profile(row, relation, viewer_id=parent.parent_id, today=today),
        growth=None if growth is None else _growth_out(growth, today),
    )
    await session.commit()
    idempotency.remember(**scope, replay=response.model_dump(mode="json"))
    return response


@router.get("/children/{cid}/growth", responses={403: {"model": ErrorEnvelope}})
async def read_growth_logs(child: AccessibleChild, session: SessionDep) -> GrowthLogsResponse:
    """키 · 몸무게 기록 — 잰 날 최신순.

    보호자가 넣은 숫자를 그대로 돌려준다. 평가 표현은 만들지 않는다 (루트 §2).
    """
    today = today_kst()
    rows = await list_growth_logs(session, child_id=child.child_id)
    return GrowthLogsResponse(items=[_growth_out(row, today) for row in rows])


@router.post(
    "/children/{cid}/growth",
    status_code=201,
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def add_growth_log(
    child: AccessibleChild, body: CreateGrowthLogRequest, parent: CurrentParent, session: SessionDep
) -> GrowthLogResponse:
    """키 · 몸무게 기록 한 줄을 더한다. 키 · 몸무게 둘 다 없으면 400."""
    today = today_kst()
    _check_measured_on(body.measured_on, birth_date=child.birth_date, today=today)
    if body.height_cm is None and body.weight_kg is None:
        raise ApiError(
            400,
            "validation_failed",
            "키나 몸무게 중 하나는 적어주세요",
            {"fields": ["height_cm", "weight_kg"]},
        )
    row = await create_growth_log(
        session,
        child_id=child.child_id,
        created_by=parent.parent_id,
        measured_on=body.measured_on,
        height_cm=body.height_cm,
        weight_kg=body.weight_kg,
    )
    response = GrowthLogResponse(log=_growth_out(row, today))
    await session.commit()
    return response


@router.patch(
    "/children/{cid}/growth/{log_id}",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403, 404)},
)
async def edit_growth_log(
    child: AccessibleChild, log_id: UUID, body: UpdateGrowthLogRequest, session: SessionDep
) -> GrowthLogResponse:
    """보낸 칸만 바꾼다. 키 · 몸무게의 null 은 비우기. 고친 결과가 둘 다 비면 400.

    아무 칸도 없으면 바꾸지 않고 지금 줄을 200 으로 돌려준다 (목과 같다).
    다른 아이의 기록 id 는 없는 것과 같다 (404).
    """
    changes = body.model_dump(exclude_unset=True)
    today = today_kst()
    if "measured_on" in changes:
        _check_measured_on(changes["measured_on"], birth_date=child.birth_date, today=today)

    row = await find_growth_log(
        session, child_id=child.child_id, log_id=log_id, for_update=bool(changes)
    )
    if row is None:
        raise ApiError(404, "not_found", "이미 지워진 기록이에요")
    if not changes:
        # 고치기 시트는 아무것도 안 바꾸고 저장하면 {} 를 보낸다 (growth-sheet.tsx).
        # 지금 줄을 그대로 돌려준다
        return GrowthLogResponse(log=_growth_out(row, today))
    height = changes.get("height_cm", row.height_cm)
    weight = changes.get("weight_kg", row.weight_kg)
    if height is None and weight is None:
        raise ApiError(
            400,
            "validation_failed",
            "키나 몸무게 중 하나는 적어주세요",
            {"fields": ["height_cm", "weight_kg"]},
        )
    for name, value in changes.items():
        setattr(row, name, value)
    await session.flush()
    await session.refresh(row)
    response = GrowthLogResponse(log=_growth_out(row, today))
    await session.commit()
    return response


@router.delete(
    "/children/{cid}/growth/{log_id}",
    status_code=204,
    responses={status: {"model": ErrorEnvelope} for status in (403, 404)},
)
async def remove_growth_log(child: AccessibleChild, log_id: UUID, session: SessionDep) -> Response:
    """기록 한 줄을 지운다. 되돌릴 수 있는 기록이라 승인 게이트가 아니다 (루트 §2)."""
    if not await delete_growth_log(session, child_id=child.child_id, log_id=log_id):
        raise ApiError(404, "not_found", "이미 지워진 기록이에요")
    await session.commit()
    return Response(status_code=204)


@router.post("/children/{cid}/inputs", status_code=202)
async def create_input(
    child: AccessibleChild,
    body: CreateInputRequest,
    parent: CurrentParent,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> CreateInputResponse:
    """접수만 한다 — 채널을 열고 러너를 뒤에서 띄우고 바로 202. Agent 를 기다리지 않는다.

    순서: (아이 접근 확인 — 의존성) → 키 확인 → 재생 → 동의 읽기 → 맥락 확인 → 한도 →
    맥락 꺼내기 → 채널 열기 → 키 기억 → 러너 시작.
    키를 러너보다 먼저 적어 두면 같은 키가 몇 ms 뒤에 또 와도 새 run이 아니라
    재생으로 흡수된다 (idempotency.py). 이어받기 맥락은 재생 뒤에 본다. 같은 키 재생은 이미 꺼낸
    맥락으로 돈 run을 돌려받아야 하고, 400 을 받으면 안 된다. 꺼내는 건 한도를 통과한 뒤다.
    재생할 run 이 failed 로 끝났으면 재생하지 않고 새 run 으로 받는다. 채널이 이미 지워졌으면
    대화 기록으로 끝났는지 알린다 (#538).
    """
    if not idempotency_key:
        raise ApiError(
            400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
        )

    scope = {
        "parent_id": parent.parent_id,
        "method": request.method,
        "path": request.url.path,
        "key": idempotency_key,
    }
    replayed = idempotency.recall(**scope)
    if replayed is not None:
        previous = registry.get(replayed)
        if previous is None:
            # 채널은 닫힌 지 5분 뒤 지워지고 키는 24시간 남는다. 끝난 run 은 대화 기록이 남아
            # 있으니 화면이 SSE(404) 대신 지난 대화로 결과를 그리게 알린다 (#538).
            # 없으면 저장됐는지 모르는 run 이다(재시작 · 기록 실패) — 키를 놓지 않고 그대로 재생한다
            day = await find_turn_date(
                session, parent_id=parent.parent_id, child_id=child.child_id, run_id=replayed
            )
            if day is not None:
                return CreateInputResponse(run_id=replayed, finished=True, conversation_date=day)
            return CreateInputResponse(run_id=replayed)
        if previous.ended_with != "failed":
            return CreateInputResponse(run_id=replayed)
        # failed 는 "저장 없음" 이라 러너가 키를 놓는데, 거기서 Redis 오류로 실패하면 키가 남는다.
        # 여기서 다시 놓고 새 run 으로 받는다. 지난 실패를 재생하면 다시 보내도 같은 실패뿐이다
        # (#538)
        idempotency.forget_run(replayed)

    # 아이 건강정보 동의의 현재 값을 입력 한 번에 한 번 읽어 Agent 까지 값으로 넘긴다 (#372).
    # 🚨 읽기에 실패하면 False 로 바꾸지 않고 예외로 끝낸다 — "동의 안 함" 으로 넘기면 Agent 가
    #    health_safety 를 읽지 않고 알레르기 필터 없이 추천을 연다. 한도 · 되묻기 맥락 · 채널 ·
    #    키 기억보다 앞이라 실패한 요청이 하루 한도와 한 번뿐인 맥락을 먹지 않는다.
    # 🚨 되묻기 맥락 확인(get)보다도 앞이다. get 과 consume 사이에 await 가 생기면 같은
    #    reply_to 로 거의 동시에 온 두 답이 둘 다 확인을 통과해 관찰이 두 행이 된다.
    #    공통 의존성(AccessibleChild)에 넣지 않았다 — 아이 주소를 쓰는 모든 요청에 쿼리가 붙는다.
    health_consent = await child_health_granted(session, child_id=child.child_id)
    # 좌표는 위치정보 동의가 있을 때만 Agent 로 넘긴다 (#380). 좌표가 안 왔으면 읽지도 않는다.
    # 동의가 없으면 버리고 실내 전용으로 돈다 — 화면이 보냈어도 동의 없이 쓰지 않는다.
    # 🚨 좌표는 저장 · 로그 · 예외 문구에 싣지 않는다. CoarseLocation 이 한 번 더 자르고
    #    repr 을 막는다. 동의 읽기와 같은 이유로 되묻기 맥락 확인(get)보다 앞이다 (await)
    location = None
    if body.location is not None and await location_granted(session, parent_id=parent.parent_id):
        location = entrypoint.CoarseLocation(lat=body.location.lat, lon=body.location.lon)

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
    # 되묻기 답도 센다. 이어받기 run 도 Memory 를 부른다. 같은 조각의 되묻기는 처음 질문까지
    # 4회가 상한이지만(#251 · agents/memory/agent.py MAX_QUESTIONS), 조각마다 새로 열리니
    # 답을 빼면 하루 한도를 비껴간다.
    if not quota.consume(
        parent_id=parent.parent_id, today=quota.today_kst(), limit=settings.INPUT_DAILY_LIMIT
    ):
        raise ApiError(429, "daily_input_limit", "오늘은 더 적을 수 없어요. 내일 다시 적어 주세요.")

    if body.reply_to is not None:
        # get 과 여기 사이에 await 가 없어서 같은 reply_to 로 온 다른 요청이 끼어들지 못한다.
        # 🚨 이 사이에 await 를 넣지 말 것 — tests/unit/api/test_create_input_consent.py 의
        #    "같은 되묻기에 거의 동시에 두 번 답하면 하나만 받는다" 가 막는다.
        # 한 번만 쓴다. 같은 질문에 두 번 답하면 관찰이 두 행이 된다
        pending_reply.consume(
            run_id=body.reply_to, parent_id=parent.parent_id, child_id=child.child_id
        )

    channel = registry.open_run(parent_id=parent.parent_id)
    try:
        idempotency.remember(**scope, replay=channel.run_id)
    except Exception:
        # 러너가 안 떠서 이 채널은 아무도 닫지 않는다. 닫아야 지울 예약이 걸린다 (#536 리뷰)
        channel.close()
        raise
    # 대화 기록(#228). 접수한 시각으로 날짜를 정한다 — run 이 끝나 행을 쓰는 시각이 아니다
    record = chat_record.TurnRecorder(
        parent_id=parent.parent_id,
        child_id=child.child_id,
        run_id=channel.run_id,
        raw_text=body.text,
        reply_to=body.reply_to,
        accepted_at=datetime.now(UTC),
    )
    # 보호자는 본문이 아니라 토큰에서 — Memory 가 작성자로 적어서 보호자의 말이 아이의 사실이
    # 되지 않는다 (§2).
    job = runner.agent_job(
        child_id=child.child_id,
        birth_date=child.birth_date,
        child_health_granted=health_consent,
        location=location,
        activity_weather=getattr(request.app.state, "activity_weather", None),
        parent_id=parent.parent_id,
        raw_text=body.text,
        continuation=continuation,
        reply_to=body.reply_to,
        on_event=record.add,
    )
    runner.start(channel, job, raw_text=body.text, on_end=record.save)
    return CreateInputResponse(run_id=channel.run_id)
