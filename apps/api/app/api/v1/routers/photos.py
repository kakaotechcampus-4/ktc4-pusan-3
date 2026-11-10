"""08 사진으로 적기 — 기관 문서를 읽어 보호자가 확인한 것만 저장한다.

🚨 여기는 승인 게이트가 아니다. commit 은 캘린더에 쓰지 않고, 일정은 보호자가 항목 시트에서
   `POST /children/{cid}/events`(게이트 ㉠)로 넣는다 (docs/web/screen-rules-v1.md §4).
🚨 활동 lane(아이가 찍힌 사진에서 태그 뽑기)은 아직 없다. 문서만 받는다.
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api import idempotency, quota
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.runs import photo_run, registry, runner
from app.api.v1.schemas.photos import (
    PhotoCommitRequest,
    PhotoCommitResponse,
    PhotoLane,
    PhotoUploadResponse,
    PhotoUploadTicket,
)
from app.core.config import settings
from app.domains.child.repository import find_accessible_child
from app.domains.daycare_meal.repository import insert_daycare_meals
from app.domains.notice.repository import save_notice
from app.domains.schedule.repository import link_notice_items
from app.integrations import photo_uploads

router = APIRouter()
log = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 15 * 1024 * 1024
_TOO_LARGE = "사진이 너무 커요. 15MB 까지 올릴 수 있어요"
_PICK_AGAIN = "사진을 다시 골라 주세요"


def _require_photo_reading() -> None:
    # 🚨 화면 버튼을 숨겨도 주소를 직접 부르면 사진이 외부 AI 로 나간다. 서버에서도 막는다
    if (
        not settings.PHOTO_UPLOAD_ACCEPT
        or not settings.MEAL_OCR_BASE_URL
        or not settings.MEAL_OCR_API_KEY
    ):
        raise ApiError(503, "photo_unavailable", "지금은 사진을 읽을 수 없어요")


@router.post(
    "/children/{cid}/photos/uploads",
    status_code=201,
    summary="사진 올릴 곳 받기",
    description=(
        "S3 버킷이 설정돼 있으면 5분짜리 업로드 주소를 준다(`mode: s3`). 화면이 사진을 S3 에 바로 "
        "올려서 서버가 사진 본문을 받지 않는다. 버킷이 없는 로컬 개발이면 `mode: direct` 다. "
        "아무것도 저장하지 않고 하루 한도도 쓰지 않는다 — 한도는 읽을 때 쓴다."
    ),
    responses={status: {"model": ErrorEnvelope} for status in (503,)},
)
async def new_photo_upload(child: AccessibleChild, parent: CurrentParent) -> PhotoUploadTicket:
    _require_photo_reading()
    if not settings.PHOTO_UPLOAD_BUCKET:
        return PhotoUploadTicket(mode="direct")
    ticket = await asyncio.to_thread(
        photo_uploads.new_upload, parent.parent_id, max_bytes=MAX_PHOTO_BYTES
    )
    return PhotoUploadTicket(
        mode="s3",
        url=ticket.url,
        fields=ticket.fields,
        key=ticket.key,
        expires_in=photo_uploads.UPLOAD_URL_TTL_SECONDS,
    )


async def _photo_bytes(file: UploadFile | None, upload_key: str | None, parent_id) -> bytes:
    """사진 바이트. S3 를 쓰면 열쇠로 내려받고 바로 지운다 — 원본을 남기지 않는다."""
    if not settings.PHOTO_UPLOAD_BUCKET:
        if file is None:
            raise ApiError(400, "validation_failed", _PICK_AGAIN, detail={"field": "file"})
        data = await file.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise ApiError(413, "photo_too_large", _TOO_LARGE)
        return data
    # 버킷이 있으면 사진은 S3 로만 받는다. 본문으로 온 사진을 받으면 S3 를 쓰는 뜻이 사라진다
    if file is not None or not upload_key or not photo_uploads.owns(upload_key, parent_id):
        raise ApiError(400, "validation_failed", _PICK_AGAIN, detail={"field": "upload_key"})
    try:
        return await asyncio.to_thread(photo_uploads.take, upload_key, max_bytes=MAX_PHOTO_BYTES)
    except photo_uploads.MissingUpload:
        raise ApiError(
            400, "validation_failed", _PICK_AGAIN, detail={"field": "upload_key"}
        ) from None
    except photo_uploads.UploadTooLarge:
        raise ApiError(413, "photo_too_large", _TOO_LARGE) from None


@router.post(
    "/children/{cid}/photos",
    status_code=202,
    summary="기관 문서 사진 올리기",
    description=(
        "읽기를 뒤에서 시작하고 바로 돌아온다. 결과는 `GET /runs/{rid}/events` 의 `parsed`. "
        "아무것도 저장하지 않는다. Idempotency-Key 필수 — 같은 사진을 두 번 읽으면 비용이 두 배다. "
        "사진은 `POST .../photos/uploads` 가 `s3` 면 `upload_key` 로, "
        "`direct` 면 `file` 로 보낸다. S3 에 올린 사진은 서버가 내려받자마자 지운다."
    ),
    responses={status: {"model": ErrorEnvelope} for status in (400, 413, 422, 429, 503)},
)
async def upload_photo(
    child: AccessibleChild,
    parent: CurrentParent,
    request: Request,
    lane: Annotated[PhotoLane, Form()],
    file: Annotated[UploadFile | None, File()] = None,
    upload_key: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> PhotoUploadResponse:
    # S3 에 올라온 사진을 읽지 않고 돌려보내는 길(스위치 · 활동 사진 · 키 재생 · 형식 오류)에서도
    # 원본을 남기지 않는다. 남의 열쇠는 건드리지 않는다 (#726 적대적 리뷰)
    own_key = bool(
        settings.PHOTO_UPLOAD_BUCKET
        and upload_key
        and photo_uploads.owns(upload_key, parent.parent_id)
    )
    taken = False
    try:
        if not idempotency_key:
            raise ApiError(
                400, "idempotency_key_required", "요청을 처리할 수 없어요. 다시 시도해 주세요"
            )
        _require_photo_reading()
        if lane != "document":
            raise ApiError(422, "lane_not_supported", "지금은 문서 사진만 읽을 수 있어요")

        scope = {
            "parent_id": parent.parent_id,
            "method": request.method,
            "path": request.url.path,
            "key": idempotency_key,
        }
        replayed = await idempotency.recall(**scope)
        if replayed is not None:
            previous = await registry.lookup(replayed)  # 다른 워커의 run 도 (#877)
            if previous is not None and previous.ended_with != "failed":
                return PhotoUploadResponse(run_id=replayed)
            idempotency.forget_run(replayed)

        data = await _photo_bytes(file, upload_key, parent.parent_id)
        taken = True  # 내려받기에서 이미 지웠다 — 실패했어도 지운다
        if not data:
            raise ApiError(400, "validation_failed", _PICK_AGAIN)

        if not await quota.consume(
            parent_id=parent.parent_id, today=quota.today_kst(), limit=settings.INPUT_DAILY_LIMIT
        ):
            raise ApiError(
                429, "daily_input_limit", "오늘은 더 적을 수 없어요. 내일 다시 적어 주세요."
            )

        channel = registry.open_run(parent_id=parent.parent_id)
        try:
            # 채널 행이 표에 쓰인 뒤에 202 — 다른 워커의 SSE · 같은 키 재시도가 이 run 을 찾는다
            # (#877 4단계 · #899 리뷰). 못 찾으면 재시도가 사진을 두 번 읽고 한도도 두 번 쓴다
            await channel.opened()
            # 아직 아무것도 읽지 않았다 — 키를 못 남기면 접수하지 않고 503 (한 줄 입력과 같다,
            # #671). 재생 확인 뒤에 같은 키가 겹쳐 왔으면 먼저 적은 요청만 읽는다 (idempotency.py)
            winner = await idempotency.remember_first(**scope, replay=channel.run_id)
        except Exception:
            channel.close()  # 러너가 안 떠서 아무도 닫지 않는다
            raise
        if winner is not None:
            # 진 요청 — 사진을 읽지 않고 먼저 적힌 run 을 돌려준다. 러너가 안 떠서 채널은 여기서
            # 닫는다. 🚨 순서는 그대로 둔다: 하루 한도는 이미 1회 썼고(한도가 키 적기보다 앞),
            #    S3 로 받은 사진은 내려받으며 지웠다(읽지 않은 사진이 버킷에 남지 않는다)
            channel.close()
            return PhotoUploadResponse(run_id=winner)
        run = photo_run.open_photo_run(
            run_id=channel.run_id, parent_id=parent.parent_id, child_id=child.child_id
        )
        job = photo_run.photo_job(run, data=data, received_at=datetime.now(UTC), declared_lane=lane)
        runner.start(channel, job, raw_text="")
        return PhotoUploadResponse(run_id=channel.run_id)
    finally:
        if own_key and not taken:
            await asyncio.to_thread(photo_uploads.discard, upload_key)


@router.post(
    "/photo-runs/{rid}/commit",
    status_code=201,
    summary="읽은 내용 저장",
    description=(
        "보호자가 확인한 항목만 저장한다. 사진 흐름에서 DB 에 쓰는 유일한 지점이다. "
        "확인하지 않은 항목이 오면 422. 일정은 만들지 않는다."
    ),
    response_model=PhotoCommitResponse,
    responses={status: {"model": ErrorEnvelope} for status in (404, 422)},
)
async def commit_photo(
    rid: str, body: PhotoCommitRequest, parent: CurrentParent, session: SessionDep
) -> JSONResponse:
    # 행을 잡는다 — 같은 저장이 두 워커에 동시에 와도 뒤 요청은 이 commit 뒤 남긴 응답을 본다 (#877)
    run = await photo_run.lock(session, rid, parent_id=parent.parent_id)
    if run is None or run.reading is None:
        raise ApiError(404, "not_found", "저장할 사진이 없어요")
    if run.response is not None:
        return JSONResponse(status_code=201, content=run.response)  # 다시 보내도 두 번 넣지 않는다
    # 읽은 뒤 30분 안에 아이와의 연결이 끊겼을 수 있다. 저장 주소에 아이가 없어서 여기서 다시 본다
    if (
        await find_accessible_child(session, child_id=run.child_id, parent_id=parent.parent_id)
        is None
    ):
        raise ApiError(403, "child_access_denied", "이 아이에 접근할 수 없어요")
    if body.lane != "document":
        raise ApiError(422, "lane_not_supported", "지금은 문서 사진만 저장할 수 있어요")

    ids = [entry.id for entry in body.entries]
    if len(ids) != len(set(ids)):
        # 같은 항목을 되풀이해 보내면 그 수만큼 저장됐다 (#726 리뷰)
        raise ApiError(
            422, "validation_failed", "같은 항목을 두 번 보냈어요", detail={"field": "entries"}
        )

    # 확인 필요 표시는 서버가 붙이고, 보호자가 시트에서 확인하면 화면이 false 로 바꿔 보낸다.
    # 그래서 여기서는 화면이 보낸 값을 본다 — run 의 값을 보면 보호자가 확인한 항목도 막힌다.
    # 남의 run 은 위에서 이미 막았으니, 이 값을 바꿔 보내는 사람은 보호자 본인이다
    unchecked = [entry.id for entry in body.entries if entry.needs_review]
    if unchecked:
        # 화면이 빼고 보내야 하는데 서버까지 왔다 — 조용히 저장하지 않는다
        raise ApiError(
            422,
            "validation_failed",
            "확인하지 않은 항목이 있어요",
            detail={"field": "entries", "ids": unchecked},
        )
    if not body.entries:
        raise ApiError(
            422, "validation_failed", "저장할 내용이 없어요", detail={"field": "entries"}
        )

    events = [entry.event_id for entry in body.entries if entry.event_id is not None]
    if len(events) != len(set(events)):
        raise ApiError(
            422, "validation_failed", "한 일정을 두 항목에 이었어요", detail={"field": "entries"}
        )

    items, meals = [], []
    item_of_event: dict[uuid.UUID, int] = {}  # 일정 id → items 안의 순서
    for entry in body.entries:
        if entry.id in run.items:
            if entry.event_id is not None:
                item_of_event[entry.event_id] = len(items)
            items.append(
                photo_run.apply_edits(
                    run.items[entry.id], title=entry.title, day=entry.date, supplies=entry.items
                )
            )
        elif entry.id in run.meals:
            row = photo_run.meal_row(run.meals[entry.id], title=entry.title, day=entry.date)
            if row is None:
                # 메뉴를 다 지운 끼니를 저장하라고 보냈다. 조용히 버리면 저장된 줄 안다
                raise ApiError(
                    422,
                    "validation_failed",
                    "메뉴를 적어 주세요",
                    detail={"field": "entries", "ids": [entry.id]},
                )
            meals.append(row)
        elif not entry.id.startswith("pe_meal_unread_"):
            raise ApiError(
                422,
                "validation_failed",
                "없는 항목이에요",
                detail={"field": "entries", "ids": [entry.id]},
            )

    saved = await save_notice(
        session,
        child_id=run.child_id,
        uploader_parent_id=parent.parent_id,
        notice=replace(run.reading.notice, items=tuple(items)),
    )
    if item_of_event:
        # 일정은 항목 시트에서 먼저 들어간다. 그 일정이 이 공지의 어느 항목에서 왔는지 여기서 잇는다
        missed = await link_notice_items(
            session,
            child_id=run.child_id,
            notice_id=saved.notice_id,
            links={event_id: saved.item_ids[index] for event_id, index in item_of_event.items()},
        )
        if missed:
            # 그 사이 지운 일정일 수 있다. 공지 저장까지 막을 일은 아니다
            log.warning("photo commit: events not linked %s", sorted(map(str, missed)))
    if meals:
        await insert_daycare_meals(
            session, child_id=run.child_id, meals=meals, source_notice_id=saved.notice_id
        )

    first_date = next((str(e.date) for e in body.entries if e.date is not None), None)
    response = PhotoCommitResponse(
        calendar_date=first_date if body.attach_to_calendar else None
    ).model_dump()
    # 응답을 기록과 같은 트랜잭션에 남긴다 — commit 이 실패하면 둘 다 없다
    await photo_run.finish(session, run, response)
    await session.commit()
    return JSONResponse(status_code=201, content=response)
