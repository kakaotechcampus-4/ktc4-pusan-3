"""지난 대화 다시 보기 — #228.

인증은 router.py 의 protected_router 가 건다. 아이 주소는 `AccessibleChild` 가 먼저 확인한다 —
연결된 보호자가 아니면 403 (deps/child.py). 대화는 쓴 보호자 본인의 개인 기록이라, 같은 아이와
연결된 다른 보호자의 대화는 보이지 않는다(403 이 아니라 없는 것처럼).

하루 단위로 준다. 대화 화면은 날짜 구분선 단위로 그리고, 캘린더 하루 화면이 그 날짜로 연다.
커서는 둘이다 — 하루 안에서 더 이른 턴은 `next_cursor`, 그날을 다 받으면 `prev_date` 로 전날.

🚨 `next_cursor` 는 자기 날짜를 품는다. `date` 를 빼면 오늘이라, 날짜 없는 커서를 자정이 지난 뒤
   보내면 새 날에 적용돼 빈 목록과 `prev_date`=어제가 나가고, 화면이 어제를 처음부터 다시 그려
   이미 보이는 턴이 두 번 생긴다. 커서가 오면 그 날짜로 읽고, `date` 가 함께 와서 다르면 400 이다.
"""

import base64
import binascii
import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.calendar_dates import check_day
from app.api.chat_replies import CHAT_REPLIES
from app.api.deps.auth import CurrentParent
from app.api.deps.child import AccessibleChild
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, ErrorEnvelope
from app.api.quota import today_kst
from app.api.v1.schemas.chat import ChatDayResponse, ChatTurnOut
from app.domains.chat.repository import TurnCursor, page_turns, previous_date

router = APIRouter()


@router.get(
    "/children/{cid}/chat",
    summary="지난 대화 하루치",
    responses={status: {"model": ErrorEnvelope} for status in (400, 403)},
)
async def get_chat_day(
    child: AccessibleChild,
    parent: CurrentParent,
    session: SessionDep,
    date: Annotated[
        dt.date | None,
        Query(
            description="한국 시간 날짜. 빼면 오늘 — cursor 가 있으면 cursor 의 날짜",
            examples=["2026-10-15"],
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            description=(
                "앞 페이지 응답의 next_cursor 를 그대로. 그날을 품고 있어 date 없이 보내도 된다. "
                "date 와 함께 보내면 같은 날이어야 한다(다르면 400)"
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ChatDayResponse:
    """그날 내가 나눈 대화. 앱을 다시 켜면 오늘을, 위로 올리면 앞 날짜를 부른다."""
    after = _decode_cursor(cursor) if cursor else None
    if after is not None and date is not None and date != after.day:
        raise ApiError(400, "validation_failed", "목록 위치가 올바르지 않아요")
    day = after.day if after is not None else date or today_kst()
    check_day(day, status=400)
    owner = {"parent_id": parent.parent_id, "child_id": child.child_id}

    turns, more = await page_turns(
        session,
        **owner,
        day=day,
        cursor=after.turn if after is not None else None,
        limit=limit,
    )
    return ChatDayResponse(
        date=day,
        turns=[
            ChatTurnOut(
                run_id=turn.run_id,
                raw_text=turn.raw_text,
                reply_to=turn.reply_to,
                created_at=turn.created_at,
                replies=CHAT_REPLIES.validate_python(turn.replies),
            )
            for turn in turns
        ],
        next_cursor=_encode_cursor(day, more) if more else None,
        prev_date=await previous_date(session, **owner, before=day),
    )


@dataclass(frozen=True)
class _DayCursor:
    day: dt.date
    turn: TurnCursor


def _encode_cursor(day: dt.date, turn: TurnCursor) -> str:
    raw = json.dumps([day.isoformat(), turn.created_at.isoformat(), str(turn.id)])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> _DayCursor:
    """깨진 커서는 400. 화면이 받은 값을 그대로 돌려주는 한 일어나지 않는다.

    세 칸이 문자열인지 먼저 본다 — 형만 틀린 커서가 500 으로 새지 않게 (observations.py 와 같다).
    """
    try:
        padded = value + "=" * (-len(value) % 4)
        parts = json.loads(base64.urlsafe_b64decode(padded))
        if not (
            isinstance(parts, list) and len(parts) == 3 and all(isinstance(p, str) for p in parts)
        ):
            raise ValueError("커서는 문자열 세 칸이다")
        day, created_at, turn_id = parts
        moment = dt.datetime.fromisoformat(created_at)
        if moment.tzinfo is None:
            raise ValueError("커서 시각에는 시간대가 있다")
        return _DayCursor(
            day=dt.date.fromisoformat(day),
            turn=TurnCursor(created_at=moment, id=uuid.UUID(turn_id)),
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ApiError(400, "validation_failed", "목록 위치가 올바르지 않아요") from exc
