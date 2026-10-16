"""대화 한 턴의 답(`chat_turn.replies`)에 담는 모양 (#228).

러너가 run 의 서버 이벤트를 이 모양으로 바꿔 쓰고, 대화 조회가 읽어 지금 상태를 채운다.
화면 SSE(`translate.py`)와 최대한 같은 이름 · 같은 필드로 둔다 — 화면이 대화 블록을 다시 쓸 수 있게.

저장 방식은 이벤트마다 다르다 (#228 10-15 댓글).

- 문장 그대로 — `note` · `guidance` · `unavailable` · `partial` · 끝 상태(`done`) ·
  `agent_result` 의 readout 문장과 묶음 이유 · 일정 초안 내용. 그 순간 서버가 한 말이라
  나중에 바뀌지 않는다. 기록 값이 문장에 들어 있어도(알레르기 이름 · 키 · 몸무게) 고치지 않는다.
  일정 초안은 DB 에 쓰지 않아서(9/17) id 로 지금 상태를 찾을 곳이 없다 — 내용을 담고, 지난
  대화에서는 "넣기" 없이 읽기 전용으로만 그린다.
- `{kind, id}` 만 — `saved` 의 관찰, `agent_result` 의 추천 id. 관찰 표가 5개라 종류도 담는다.
  관찰은 조회할 때 지금 상태로 채운다 — 지운 관찰은 내용 없이 "지웠어요" 로(`v1/schemas/chat.py`).
  추천은 id 그대로 둔다 — 블록은 "후보 보기" 만 세우고, 채택 · 만료는 05 가 지금 상태로 거른다.
- 저장하지 않음 — `step`(진행 중에만 의미가 있다), 되묻기 조각 원문(`PendingReply`), 05 보관함
  맥락(`RetryContext` · `EmptyResult`), 로그용 이벤트. 화면 전용인 `ping`(연결 유지) · `offer` 는
  서버 이벤트가 아니라 애초에 여기 오지 않는다.

🚨 저장한 행은 고치지 않는다. 필드를 빼거나 뜻을 바꾸면 이미 쌓인 행을 못 읽는다 — 늘릴 때는
   기본값이 있는 필드로 늘린다.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredRef(_Reply):
    """가리키는 행. 관찰이면 `observation_food` 처럼 표 이름이 kind 다."""

    kind: str
    id: str


class NoteReply(_Reply):
    """Memory 가 한 말. 저장한 내용을 알리는 말이라 관찰 내용이 들어 있을 수 있다.

    그날 오간 말로 두고 지금 기록으로 고치지 않는다 (입력 원문과 같다).
    """

    type: Literal["note"] = "note"
    text: str
    kind: Literal["question", "message"]


class GuidanceReply(_Reply):
    type: Literal["guidance"] = "guidance"
    code: str
    message: str
    deeplink: str | None


class UnavailableReply(_Reply):
    type: Literal["unavailable"] = "unavailable"
    agents: list[str]


class SavedReply(_Reply):
    """이 run 에서 저장한 관찰. 내용은 담지 않는다."""

    type: Literal["saved"] = "saved"
    refs: list[StoredRef]


class EventDraftReply(_Reply):
    """일정 초안 내용(`core/event_draft.py` 의 payload 그대로). 지난 대화에서는 읽기 전용이다."""

    type: Literal["event_draft"] = "event_draft"
    drafts: list[dict[str, Any]]


class StoredReadout(_Reply):
    """`agent_result` 의 readout 한 덩어리. 종류와 상관없이 문장 그대로 담는다.

    알레르기 확인 문구 · 성장 추이처럼 아이 기록 값이 문장에 들어 있어도 그날 오간 말로 둔다
    (입력 원문 · `note` 와 같다). 근거(`source_refs`)는 담지 않는다 — 근거로 문장을 다시 그리는
    길이 생기면 그때 기본값이 있는 칸으로 늘린다.
    """

    kind: str
    title: str
    body: str
    authored_by: Literal["code", "model"]
    code: str | None


class AgentResultReply(_Reply):
    type: Literal["agent_result"] = "agent_result"
    agent: str
    task_type: str | None
    status: str
    readouts: list[StoredReadout]
    question: str | None
    suggestion_ids: list[str]
    kind: Literal["personalized", "general"] | None
    reason: str | None
    record_count: int | None


class PartialReply(_Reply):
    type: Literal["partial"] = "partial"
    reason: str
    succeeded: list[str]
    failed: list[str]
    retryable: list[str]


class DoneReply(_Reply):
    """끝 상태. `completed=False` 는 저장한 뒤에 끊긴 run 이다 (화면 `DoneEvent.completed`)."""

    type: Literal["done"] = "done"
    completed: bool


ChatReply = Annotated[
    NoteReply
    | GuidanceReply
    | UnavailableReply
    | SavedReply
    | EventDraftReply
    | AgentResultReply
    | PartialReply
    | DoneReply,
    Field(discriminator="type"),
]

CHAT_REPLIES: TypeAdapter[list[ChatReply]] = TypeAdapter(list[ChatReply])
"""`chat_turn.replies` 를 읽고 쓰는 어댑터.

쓸 때 `dump_python(..., mode="json")`, 읽을 때 `validate_python`.
"""
