import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk


class ChatTurn(Base, UUIDPk):
    """대화 한 턴 — 보호자 입력 하나와 그 run 의 답 전체 (#228).

    보호자 개인 기록이다. 쓴 보호자 본인만 보고, 일기(`diary_entry`)처럼 작성자가 탈퇴하면
    함께 지워진다(CASCADE). 연결 끊기는 보호자 행이 남아 CASCADE 가 움직이지 않으니
    `unlink_parent` 가 직접 지운다 (#228 10-06 댓글 · docs/safety/retention-v1.md).

    - `done` 으로 끝난 run 만 저장한다. `failed` 는 저장된 것도 보여 준 결과도 없는 run 이고,
      원문은 입력창으로 돌아가 다시 보내면 새 run 이 된다 — 저장하면 같은 말풍선이 두 번 생긴다.
    - `conversation_date` 는 입력을 접수한 순간의 KST 날짜다. 되묻기에 답한 run 은 질문 턴의
      날짜를 따른다 — 자정을 넘겨 온 답도 질문한 날에 붙는다 (#226 과 같은 규칙).
    - `reply_to` 는 FK 가 아니다. 질문 행은 run 이 끝난 뒤에 쓰는데 되묻기 맥락은 그보다 먼저
      열려서, 빨리 답하면 질문 행이 아직 없을 수 있다.
    - `replies` 의 모양은 `app/api/chat_replies.py` 가 정한다. 관찰 · 추천은 내용이 아니라
      `{kind, id}` 만 담는다 — 조회할 때 지금 상태를 읽는다.
    - 한 번 쓰면 고치지 않아서 `updated_at` 이 없다. `created_at` 은 행을 쓴 시각이 아니라
      입력을 접수한 시각이다.
    """

    __tablename__ = "chat_turn"
    __table_args__ = (
        # 날짜별 조회 · 이전 날 커서 · 하루 안 페이지 · 연결 끊기 삭제가 모두 이 순서로 찾는다
        Index(
            "ix_chat_turn_parent_child_date",
            "parent_id",
            "child_id",
            "conversation_date",
            "created_at",
        ),
    )

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    # registry.open_run 의 uuid4().hex. suggestion.run_id 와 같은 타입이다
    run_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    conversation_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to: Mapped[str | None] = mapped_column(Text)
    replies: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
