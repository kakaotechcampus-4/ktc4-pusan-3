"""05 다시 시도 보관함 — (run_id, agent) → 실패 맥락 15분 · 0개 기록 · 닫힘 기록 24시간 (#249).

입력 run 의 pipeline 이 화면에 안 나가는 이벤트(RetryContext · EmptyResult)로 내면 러너가 넣고,
05 추천 엔드포인트가 판단 순서 ② · ③ 에서 꺼낸다. "추천 있음" 은 DB(`suggestion.run_id`)로 본다.
05 가 다시 만든 결과가 0개면 0개 기록, 닫힘(blocked · unsupported)이면 guard 기록을 남긴다 —
다음 요청이 같은 모양을 재생한다.

- 표(`run_hold`, UNLOGGED)에 둔다 (#877 2단계). 워커가 여럿이어도 05 가 같은 맥락을 본다.
  한 칸(run_id, agent)에 실패 맥락 · 0개 기록 · 닫힘 기록 중 하나가 든다 — payload 의 `type`.
- 실패 맥락에는 원문 조각(request_texts)이 있다. 로그를 남기지 않는다. 만료가 지나면 꺼낼 수 없고
  `hold_purge` 가 1분마다 지운다. 탈퇴 · 아이 삭제는 FK CASCADE, 연결 끊기는 `delete_link_holds`.
- 꺼낼 때 "사용 중" 표시만 한다(#246 되묻기 보관함과 같은 방식). 다시 만들기가 성공하면 지우고,
  실패하면 표시만 푼다. 15분은 처음 넣은 시각부터 센다 — 다시 넣어도 갱신하지 않는다.
- 함수마다 자기 세션으로 바로 commit 한다 (`pending_reply` 와 같은 이유).
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.db import background_session
from app.api.runs.tidy import tidy
from app.domains.run_hold.models import HoldKind
from app.domains.run_hold.repository import (
    claim_hold,
    drop_hold,
    get_hold,
    hold_in_use,
    put_hold,
    release_hold,
)

log = logging.getLogger(__name__)

FAILED_TTL = timedelta(minutes=15)
RECORD_TTL = timedelta(hours=24)
BUSY: Final = "busy"

_KIND = HoldKind.SUGGESTION_RETRY
_FAILED, _EMPTY, _GUARD = "failed", "empty", "guard"

_session_factory: Callable[[], AsyncSession] = background_session


@dataclass(frozen=True)
class RetryTask:
    task_type: str | None
    request_texts: tuple[str, ...]
    sent_at: datetime  # 입력 run 의 기준 시각. 다시 만들 때 Agent context 의 now
    # 앞 대화를 이어 묻는 task 였으면 보호자 원문 · 앞 턴의 말. 다시 만들 때 같은 출처 확인을 한다
    original_texts: tuple[str, ...] = ()
    prior_texts: tuple[str, ...] = ()


_TASK = TypeAdapter(RetryTask)


def _key(run_id: str, agent: str) -> str:
    return f"{run_id}:{agent}"


async def _put(
    *,
    run_id: str,
    agent: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    payload: dict,
    ttl: timedelta,
    now: datetime | None,
    keep_failed_expiry: bool = False,
) -> None:
    """넣는다. 보호자 · 아이가 이미 지워졌으면(늦게 끝난 run) 남기지 않고 넘어간다."""
    async with _session_factory() as session:
        try:
            await put_hold(
                session,
                kind=_KIND,
                key=_key(run_id, agent),
                parent_id=parent_id,
                child_id=child_id,
                payload=payload,
                ttl=ttl,
                now=now,
                keep_expiry_of_type=_FAILED if keep_failed_expiry else None,
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
            log.warning("run %s 의 05 보관을 넣지 않았다 — 보호자 · 아이가 없다", run_id)


async def _read(
    *, run_id: str, agent: str, parent_id: uuid.UUID, child_id: uuid.UUID, now: datetime | None
) -> dict | None:
    """주인 · 만료 · 사용 중 아님을 본다(`get_hold`). 0개 · 닫힘 기록은 사용 중 표시가 붙지 않아서
    늘 읽히고, 사용 중인 실패 맥락은 읽히지 않는다 — 그 칸은 BUSY 로 claim 이 판단한다."""
    async with _session_factory() as session:
        payload = await get_hold(
            session,
            kind=_KIND,
            key=_key(run_id, agent),
            parent_id=parent_id,
            child_id=child_id,
            now=now,
        )
    return payload


async def put_failed(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    task: RetryTask,
    now: datetime | None = None,
) -> None:
    await _put(
        run_id=run_id,
        agent=agent,
        parent_id=parent_id,
        child_id=child_id,
        payload={"type": _FAILED, "task": _TASK.dump_python(task, mode="json")},
        ttl=FAILED_TTL,
        now=now,
        keep_failed_expiry=True,
    )


async def put_empty(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    message: str,
    now: datetime | None = None,
) -> None:
    await _put(
        run_id=run_id,
        agent=agent,
        parent_id=parent_id,
        child_id=child_id,
        payload={"type": _EMPTY, "message": message},
        ttl=RECORD_TTL,
        now=now,
    )


async def put_guard(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    code: str,
    message: str,
    now: datetime | None = None,
) -> None:
    await _put(
        run_id=run_id,
        agent=agent,
        parent_id=parent_id,
        child_id=child_id,
        payload={"type": _GUARD, "code": code, "message": message},
        ttl=RECORD_TTL,
        now=now,
    )


async def guard_of(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    now: datetime | None = None,
) -> tuple[str, str] | None:
    payload = await _read(
        run_id=run_id, agent=agent, parent_id=parent_id, child_id=child_id, now=now
    )
    if payload is None or payload.get("type") != _GUARD:
        return None
    return payload["code"], payload["message"]


async def empty_message(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    now: datetime | None = None,
) -> str | None:
    payload = await _read(
        run_id=run_id, agent=agent, parent_id=parent_id, child_id=child_id, now=now
    )
    if payload is None or payload.get("type") != _EMPTY:
        return None
    return payload["message"]


async def claim(
    *,
    run_id: str,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    agent: str,
    now: datetime | None = None,
) -> RetryTask | Literal["busy"] | None:
    """실패 맥락을 꺼내 "사용 중" 으로 둔다. 다른 요청이 쓰는 중이면 BUSY.

    🚨 확인과 표시가 DB 한 문장이다 — 같은 맥락으로 온 두 요청이 워커가 달라도 둘 다 다시 만들지
       않는다.
    """
    where = {
        "kind": _KIND,
        "key": _key(run_id, agent),
        "parent_id": parent_id,
        "child_id": child_id,
        "now": now,
        "payload_type": _FAILED,
    }
    async with _session_factory() as session:
        payload = await claim_hold(session, **where)
        await session.commit()
        if payload is None:
            return BUSY if await hold_in_use(session, **where) else None
    try:
        return _TASK.validate_python(payload["task"])
    except Exception as exc:
        # 🚨 RetryTask 모양을 바꾼 배포 뒤의 옛 행. 검증 오류의 글에 원문 조각이 실리니 종류만
        #    남기고, 행을 지워 "이어서 할 수 없어요" 로 끝낸다 — 올리면 15분 동안 BUSY (#899 리뷰)
        log.warning("run %s 의 05 실패 맥락을 읽지 못해 지운다 — %s", run_id, type(exc).__name__)
        # 🚨 tidy 로 감싼다 — except 안에서 터지면 새 예외의 __context__ 로
        #    검증 오류(원문)가 함께 찍힌다
        await tidy(drop(run_id=run_id, agent=agent), "05 실패 맥락 지우기", run_id)
        return None


async def release(*, run_id: str, agent: str) -> None:
    async with _session_factory() as session:
        await release_hold(session, kind=_KIND, key=_key(run_id, agent))
        await session.commit()


async def drop(*, run_id: str, agent: str) -> None:
    async with _session_factory() as session:
        await drop_hold(session, kind=_KIND, key=_key(run_id, agent))
        await session.commit()
