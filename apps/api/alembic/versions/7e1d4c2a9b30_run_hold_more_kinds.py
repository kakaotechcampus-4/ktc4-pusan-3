"""run_hold 에 05 다시 시도 · 사진 run 을 더한다 (#877 2단계)

허용하는 kind 를 넓히기만 한다 — 앞 코드는 새 값을 쓰지 않으므로 롤백해도 돈다.

Revision ID: 7e1d4c2a9b30
Revises: 3c5b651dc02e
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7e1d4c2a9b30"
down_revision: str | Sequence[str] | None = "3c5b651dc02e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_run_hold_kind", "run_hold", type_="check")
    # expand-ok: 허용 값을 넓히기만 한다 (pending_reply → + suggestion_retry · photo_run)
    op.create_check_constraint(
        "ck_run_hold_kind",
        "run_hold",
        "kind IN ('pending_reply', 'suggestion_retry', 'photo_run')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM run_hold WHERE kind <> 'pending_reply'")
    op.drop_constraint("ck_run_hold_kind", "run_hold", type_="check")
    op.create_check_constraint("ck_run_hold_kind", "run_hold", "kind IN ('pending_reply')")
