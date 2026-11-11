"""삭제 제안 — AI 가 고른 지울 후보를 보호자가 버튼으로 확정할 때까지 둔다 (#535).

대화로 "지워줘" 하면 Memory 는 지우지 않고 제안 한 건을 남긴다. 보호자가 카드에서 체크를 고쳐
[N건 삭제하기] 를 누르면 서버가 **후보 안의 체크한 것만** 지운다. 되돌릴 수 없는 삭제를 사람이
승인하는 자리다 (루트 §2 · #535 본문 "승인 게이트로서의 의미").
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, UUIDPk
from app.infra.db.types import enum_col_py


class DeleteProposalStatus(enum.StrEnum):
    """저장하는 상태. 만료는 저장하지 않는다 — `expires_at` 이 지난 pending 이 만료다.

    배치로 바꾸지 않는 이유: 만료를 놓쳐도 삭제가 열려 있으면 안 되는데, 읽을 때 판정하면
    배치가 늦거나 멈춰도 늘 맞다 (repository.effective_status).
    """

    PENDING = "pending"
    DELETED = "deleted"
    CANCELLED = "cancelled"


class DeleteProposal(Base, UUIDPk):
    """삭제 제안 1건 = 대화 run 1개의 삭제 카드 하나.

    - 보호자 개인 대화에서 나온 것이라 **제안한 보호자만** 확인 · 취소한다. 아이와 연결이 끊긴
      보호자는 자기 제안이라도 못 누른다(라우터가 아이 접근부터 확인한다).
    - `tabs` 는 종류별 후보 목록이다. 원문을 담지 않는다 — (종류, id) 와 AI 가 고른 id(기본 체크),
      잘림 여부만. 카드에 그릴 내용은 그때마다 행을 다시 읽는다. 지워 달라고 한 내용의 사본을
      따로 남기면 삭제가 무의미해진다 (`chat_turn` 과 같은 이유).
      모양: `[{"kind": "observation_food", "truncated": true, "ids": [...], "picked": [...]}]`
      kind 는 `Saved` 의 ref 와 같은 값 — 관찰은 표 이름(`observation_food` …), 일정은 `event`.
    - `result` 는 끝난 뒤 종류별로 실제 지운 건수다(`{"observation_food": 30}`). 한 줄 요약
      ("식사 30건 · 놀이 12건을 삭제했어요") 이 이 값을 읽는다. 지운 행은 다시 읽을 수 없어서
      숫자로 남긴다.
    - parent_id · child_id 는 ON DELETE CASCADE — 탈퇴 · 아이 삭제와 함께 사라진다.
    """

    __tablename__ = "delete_proposal"
    __table_args__ = (Index("ix_delete_proposal_parent_child", "parent_id", "child_id"),)

    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("child.id", ondelete="CASCADE"), nullable=False
    )
    # 제안을 만든 run. 한 run 에 제안은 하나다 — 대화 기록(`chat_turn.run_id`)과 같은 값
    run_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tabs: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[DeleteProposalStatus] = mapped_column(
        enum_col_py(DeleteProposalStatus, "delete_proposal_status"),
        nullable=False,
        default=DeleteProposalStatus.PENDING,
        server_default=DeleteProposalStatus.PENDING.value,
    )
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
