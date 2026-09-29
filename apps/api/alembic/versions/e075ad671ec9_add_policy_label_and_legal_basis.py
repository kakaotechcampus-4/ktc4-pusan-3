"""policy_version 에 제목(label) · 법적 근거(legal_basis)를 더한다 (#168 리뷰)

보호자가 체크박스 옆에서 보는 제목과 법적 근거는 **그 버전의 글**이다. 코드(catalog.py) 한 곳에
두면 제목을 바꾸는 순간 옛 버전에 동의한 기록에도 새 제목이 소급 적용된다 — 예: 아이 건강 약관에
항목이 늘어 제목을 고치면, 예전 보호자가 보지 않은 제목이 그 동의 기록에 붙는다.
그래서 본문(content)처럼 버전마다 저장한다. 필수 · 민감 여부는 서버가 지키는 규칙이라 코드에 남긴다.

이미 있는 draft-0 네 행에는 지금 화면에 보이는 제목 · 근거를 채운다. 값은 이 파일에 고정한다 —
catalog.py 를 import 하지 않는 이유는 마이그레이션이 실행 시점의 코드에 따라 결과가 달라지면
안 되기 때문이다 (draft-0 의 시드가 상수인 것과 같은 이유).

🚨 label 은 NOT NULL 이다. 채우지 못한 행이 있으면(마이그레이션 밖에서 손으로 넣은 행) NOT NULL 을
   거는 데서 멈춘다 — 제목을 지어내 채우지 않는다.

Revision ID: e075ad671ec9
Revises: c5a81f0d3b62
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e075ad671ec9"
down_revision: Union[str, Sequence[str], None] = "c5a81f0d3b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DRAFT_0 = {
    "service_terms": ("서비스 이용약관", None),
    "privacy_account": ("개인정보 수집·이용 (보호자 본인)", None),
    "child_basic": (
        "개인정보 수집·이용 (아이 기본정보)",
        "개인정보보호법 제22조의2 (만 14세 미만 아동의 법정대리인 동의)",
    ),
    "child_health": (
        "민감정보 처리 (아이 건강·알레르기)",
        "개인정보보호법 제23조 (민감정보의 처리, 별도 동의)",
    ),
}
"""scope → (제목, 법적 근거). draft-0 이 화면에 보이던 동안의 값이다."""

policy_version = sa.table(
    "policy_version",
    sa.column("scope", sa.String),
    sa.column("version", sa.String),
    sa.column("label", sa.Text),
    sa.column("legal_basis", sa.Text),
)


def upgrade() -> None:
    op.add_column("policy_version", sa.Column("label", sa.Text(), nullable=True))
    op.add_column("policy_version", sa.Column("legal_basis", sa.Text(), nullable=True))
    for scope, (label, legal_basis) in DRAFT_0.items():
        op.execute(
            policy_version.update()
            .where(policy_version.c.scope == scope, policy_version.c.version == "draft-0")
            .values(label=label, legal_basis=legal_basis)
        )
    op.alter_column("policy_version", "label", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    op.drop_column("policy_version", "legal_basis")
    op.drop_column("policy_version", "label")
