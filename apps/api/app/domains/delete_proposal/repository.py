"""삭제 제안(`delete_proposal`)의 DB 접근 함수 (#535).

제안을 쓰는 곳은 대화 run(Memory 의 제안 tool → store) 하나, 확정하는 곳은 버튼 API 하나다.
모든 조회가 보호자 · 아이 둘 다로 거른다 — 제안한 보호자의 것만 보인다.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.delete_proposal.models import DeleteProposal, DeleteProposalStatus

PROPOSAL_TTL = timedelta(hours=24)
"""승인 없는 Suggestion 과 같은 24시간 (루트 §2 "승인 없는 Suggestion 은 24시간 뒤 만료")."""

EffectiveStatus = Literal["pending", "deleted", "cancelled", "expired"]

KINDS = frozenset(
    {
        "observation_food",
        "observation_health",
        "observation_education",
        "observation_activity",
        "observation_routine",
        "event",
    }
)
"""제안에 담을 수 있는 종류 — 조회 tool 하나 = 탭 하나 (#535). `Saved` 의 ref 와 같은 이름이다.
알레르기 · 만성질환(`health_safety`)은 없다 — 대화로 지우는 대상이 아니다 (루트 §2 ㉡)."""

TAB_MAX_IDS = 40
"""탭 하나의 후보 수 상한. 확정 요청의 id 상한(`schemas/delete_proposals.MAX_CONFIRM_IDS`)이 이
값에서 나온다. agents 의 `DELETE_TAB_MAX_IDS` 와 같은 값이다."""


@dataclass(frozen=True)
class ProposalTab:
    """한 종류의 후보. `ids` 는 모델이 조회해 본 순서 그대로, `picked` 는 그중 AI 가 고른 것."""

    kind: str
    truncated: bool
    ids: tuple[str, ...]
    picked: tuple[str, ...]

    def to_json(self) -> dict:
        # id 는 늘 글자로 둔다 — 버튼 API 가 받은 id(글자)와 비교한다
        return {
            "kind": self.kind,
            "truncated": self.truncated,
            "ids": [str(oid) for oid in self.ids],
            "picked": [str(oid) for oid in self.picked],
        }

    @classmethod
    def from_json(cls, raw: dict) -> "ProposalTab":
        return cls(
            kind=raw["kind"],
            truncated=bool(raw.get("truncated", False)),
            ids=tuple(raw.get("ids", ())),
            picked=tuple(raw.get("picked", ())),
        )


def tabs_of(row: DeleteProposal) -> list[ProposalTab]:
    return [ProposalTab.from_json(raw) for raw in row.tabs]


def candidates_of(row: DeleteProposal) -> set[tuple[str, str]]:
    """확정할 수 있는 (종류, id) 전부. 버튼 API 가 체크한 id 를 이 안에서만 받는다."""
    return {(tab.kind, oid) for tab in tabs_of(row) for oid in tab.ids}


def effective_status(row: DeleteProposal, now: datetime) -> EffectiveStatus:
    """저장한 상태 + 만료. 결정 전인데 `expires_at` 이 지났으면 만료다."""
    if row.status is DeleteProposalStatus.PENDING:
        return "expired" if row.expires_at <= now else "pending"
    return row.status.value


def _check(tabs: Sequence[ProposalTab]) -> None:
    """제안 모양을 막는다. 코드(제안 tool)가 만드는 값이라 어기면 버그다 — ValueError."""
    if not tabs:
        raise ValueError("후보가 없는 제안은 만들지 않는다")
    kinds = [tab.kind for tab in tabs]
    if len(set(kinds)) != len(kinds):
        raise ValueError("종류마다 탭은 하나다")
    if not set(kinds) <= KINDS:
        raise ValueError("제안에 담을 수 없는 종류다")
    for tab in tabs:
        if not tab.ids:
            raise ValueError("후보가 없는 탭은 넣지 않는다")
        if len(tab.ids) > TAB_MAX_IDS:
            raise ValueError(f"탭의 후보는 {TAB_MAX_IDS}건까지다")
        if len(set(tab.ids)) != len(tab.ids) or len(set(tab.picked)) != len(tab.picked):
            raise ValueError("한 탭 안에 같은 id 가 두 번 있다")
        if not set(tab.picked) <= set(tab.ids):
            raise ValueError("고른 id 는 후보 안에 있어야 한다")


async def add_proposal(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    run_id: str,
    tabs: Sequence[ProposalTab],
    now: datetime,
) -> uuid.UUID:
    """제안 한 건을 쓰고 id 를 돌려준다. commit 은 부르는 쪽(run 의 Memory commit)이 한다."""
    _check(tabs)
    row = DeleteProposal(
        parent_id=parent_id,
        child_id=child_id,
        run_id=run_id,
        tabs=[tab.to_json() for tab in tabs],
        status=DeleteProposalStatus.PENDING,
        created_at=now,
        expires_at=now + PROPOSAL_TTL,
    )
    session.add(row)
    await session.flush()
    return row.id


async def find_proposal(
    session: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
    for_update: bool = False,
) -> DeleteProposal | None:
    """제안한 보호자 · 그 아이의 제안. 남의 제안 · 다른 아이의 제안은 없는 것과 같다(None).

    for_update — 확정 · 취소가 행을 잡는다. 두 번 눌러 동시에 들어와도 한 번만 지운다.
    """
    stmt = select(DeleteProposal).where(
        DeleteProposal.id == proposal_id,
        DeleteProposal.parent_id == parent_id,
        DeleteProposal.child_id == child_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def find_proposals(
    session: AsyncSession,
    *,
    proposal_ids: Iterable[uuid.UUID],
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
) -> dict[uuid.UUID, DeleteProposal]:
    """지난 대화가 한 번에 읽는다. 없는 id 는 결과에서 빠진다."""
    ids = list(set(proposal_ids))
    if not ids:
        return {}
    rows = await session.scalars(
        select(DeleteProposal).where(
            DeleteProposal.id.in_(ids),
            DeleteProposal.parent_id == parent_id,
            DeleteProposal.child_id == child_id,
        )
    )
    return {row.id: row for row in rows}


async def finish(
    session: AsyncSession,
    row: DeleteProposal,
    *,
    status: Literal[DeleteProposalStatus.DELETED, DeleteProposalStatus.CANCELLED],
    result: dict[str, int] | None,
    now: datetime,
) -> None:
    """확정 · 취소. 부르는 쪽이 `find_proposal(for_update=True)` 로 잡은 행을 넘긴다.

    🚨 만료도 여기서 막는다 — 부르는 쪽이 상태만 보고 넘겨도 만료된 제안으로는 지울 수 없다.
    """
    if status not in (DeleteProposalStatus.DELETED, DeleteProposalStatus.CANCELLED):
        raise ValueError("확정 · 취소만 한다")
    if effective_status(row, now) != "pending":
        raise ValueError("이미 끝났거나 만료된 제안이다")
    row.status = status
    row.result = result
    row.decided_at = now
    await session.flush()
