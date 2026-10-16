"""run 하나를 대화 한 턴(`chat_turn`)으로 남긴다 (#228).

러너의 emit 이 Agent 이벤트를 하나씩 `TurnRecorder.add` 에 넘기고, 끝 신호가 나간 뒤 러너가
`save` 를 부른다. 화면으로 번역된 SSE 가 아니라 번역 전 서버 이벤트에서 모은다 — 화면에
보내지 않는 `Saved` 의 관찰 id 도 여기서는 담는다. 모양과 저장 방식은 `app/api/chat_replies.py`.

- `done` 으로 끝난 run 만 남긴다. `failed` 는 저장된 것도 보여 준 결과도 없는 run 이고, 원문은
  입력창으로 돌아가 다시 보내면 새 run 이 된다 — 남기면 같은 말풍선이 두 번 생긴다.
- 끝 신호는 pipeline 의 `Done` 말고 러너가 직접 채널에 넣기도 한다(시간 초과 · 예외 뒤
  `completed: false`). 그래서 끝 상태는 이벤트가 아니라 채널에 실제로 나간 마지막 신호에서 읽는다.
- 🚨 남기다 실패해도 run 은 그대로다. 보호자는 이미 결과를 받았고 관찰도 저장됐다 — 대화 기록
  때문에 결과를 실패로 보이게 하지 않는다. 로그에는 원문 없이 run_id 와 예외 종류만 남긴다 (§2).
"""

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agents.entrypoint import (
    AgentResult,
    Event,
    EventDrafts,
    Guidance,
    MemoryNote,
    Partial,
    Saved,
    Unavailable,
)
from app.api.chat_replies import (
    CHAT_REPLIES,
    AgentResultReply,
    ChatReply,
    DoneReply,
    EventDraftReply,
    GuidanceReply,
    NoteReply,
    PartialReply,
    SavedReply,
    StoredReadout,
    StoredRef,
    UnavailableReply,
)
from app.api.deps.db import background_session
from app.api.runs.registry import RunChannel
from app.domains.chat.repository import add_turn, find_turn_date

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


def to_reply(event: Event) -> ChatReply | None:
    """이벤트 하나를 대화에 남길 모양으로. 남기지 않을 것이면 None.

    끝 신호(`Done` · `Failed`)도 None 이다 — 끝 상태는 `TurnRecorder.save` 가 채널에서 읽는다.
    필드를 하나씩 옮겨 적는다(`asdict` 로 통째로 넘기지 않는다) — agents 가 이벤트에 필드를
    늘려도 대화 표에 새어 들어가지 않게 (`translate.py` 와 같은 이유).
    """
    if isinstance(event, MemoryNote):
        return NoteReply(text=event.text, kind=event.kind)
    if isinstance(event, Guidance):
        return GuidanceReply(code=event.code, message=event.message, deeplink=event.deeplink)
    if isinstance(event, Unavailable):
        return UnavailableReply(agents=list(event.agents))
    if isinstance(event, Saved):
        return SavedReply(refs=[StoredRef(kind=ref.kind, id=str(ref.id)) for ref in event.refs])
    if isinstance(event, EventDrafts):
        return EventDraftReply(drafts=[draft.to_payload() for draft in event.drafts])
    if isinstance(event, AgentResult):
        return AgentResultReply(
            agent=event.agent,
            task_type=event.task_type,
            status=event.status,
            readouts=[
                StoredReadout(
                    kind=readout.kind,
                    title=readout.title,
                    body=readout.body,
                    authored_by=readout.authored_by,
                    code=readout.code,
                )
                for readout in event.readouts
            ],
            question=event.question,
            suggestion_ids=list(event.suggestion_ids),
            kind=event.kind,
            reason=event.reason,
            record_count=event.record_count,
        )
    if isinstance(event, Partial):
        return PartialReply(
            reason=event.reason,
            succeeded=list(event.succeeded),
            failed=list(event.failed),
            retryable=list(event.retryable),
        )
    return None


class TurnRecorder:
    """run 하나 몫. 접수 창구가 만들어 러너에 넘긴다."""

    def __init__(
        self,
        *,
        parent_id: uuid.UUID,
        child_id: uuid.UUID,
        run_id: str,
        raw_text: str,
        reply_to: str | None,
        accepted_at: datetime,
    ):
        self.parent_id = parent_id
        self.child_id = child_id
        self.run_id = run_id
        self.raw_text = raw_text
        self.reply_to = reply_to
        self.accepted_at = accepted_at
        """입력을 접수한 시각. 행을 쓰는 시각(run 이 끝난 뒤)이 아니다.
        대화 날짜도 이걸로 정한다."""
        self.replies: list[ChatReply] = []

    def add(self, event: Event) -> None:
        """러너의 emit 안에서 불린다. 🚨 예외를 밖으로 내지 않는다 — 대화 기록 때문에 run 이
        끊기면 안 된다(모듈 머리말). 못 옮긴 이벤트는 빼고 남긴다."""
        try:
            reply = to_reply(event)
        except Exception as exc:
            log.error(
                "run %s 의 %s 를 대화 기록으로 옮기지 못했다 (%s)",
                self.run_id,
                type(event).__name__,
                type(exc).__name__,
            )
            return
        if reply is not None:
            self.replies.append(reply)

    async def save(self, channel: RunChannel) -> None:
        """끝 신호가 나간 뒤에 부른다. `done` 이 아니면 아무것도 하지 않는다."""
        if channel.ended_with != "done":
            return
        try:
            _, payload = channel.events[-1]
            done = DoneReply(completed=payload.get("completed", True))
            replies = CHAT_REPLIES.dump_python([*self.replies, done], mode="json")
            async with background_session() as session:
                day = None
                if self.reply_to is not None:
                    # 되묻기에 답한 run 은 질문한 날에 붙는다. 질문 행이 아직 없으면(빨리 답한
                    # 경우) 접수한 날이다
                    day = await find_turn_date(
                        session,
                        parent_id=self.parent_id,
                        child_id=self.child_id,
                        run_id=self.reply_to,
                    )
                await add_turn(
                    session,
                    parent_id=self.parent_id,
                    child_id=self.child_id,
                    run_id=self.run_id,
                    conversation_date=day or self.accepted_at.astimezone(KST).date(),
                    raw_text=self.raw_text,
                    reply_to=self.reply_to,
                    replies=replies,
                    created_at=self.accepted_at,
                )
                await session.commit()
        except Exception as exc:
            log.error("run %s 의 대화 기록을 남기지 못했다 (%s)", self.run_id, type(exc).__name__)
