"""suggestion_evidence 테이블을 만들고 suggestion.kind 를 더한다

근거를 `suggestion.source_refs` jsonb 에 두면 "근거 0행인 개인화 추천" 을 COUNT 로 셀 수
없다. 품질 지표가 그 값을 하드 기준 0건으로 보기 때문에 행으로 뺀다
(루트 CLAUDE.md §2 · docs/agents/data_model.md).

컬럼 이름이 `memory_*` 가 아니라 `source_*` 인 것은 가리키는 대상이 Memory 소유 테이블만이
아니어서다. 문서 행(`*_doc`)과 `daycare_meal` 도 들어온다.

`source_id` 에 FK 를 걸지 않는다. 다형 참조라 대상 테이블이 행마다 다르다 —
`correction.target_id` 와 같은 패턴이고, 대상이 있는지와 같은 `child_id` 인지는 서버가 본다.

`source_updated_at` 은 인용할 때 읽은 원본의 시각이다. 아이 기록은 그 행의 `updated_at`,
문서 행은 `written_at` 이 들어온다. 추천이 나간 뒤 근거가 바뀌었는지를 한 번의 비교로 안다.

`note` 는 그 행에서 추천 근거로 채택한 내용이고 Agent 가 쓴다. 보호자 화면에 그대로 나가는
값이라 비워 둘 수 없다.

`kind` 는 코드가 정한다. 아이 기록 근거가 1행 이상이면 `personalized`, 0행이면 `general` 이다.
기존 행은 `source_refs` 가 비었는지로 갈라 채운다.

`source_refs` 행을 `suggestion_evidence` 로 옮기지 않는다. `source_updated_at` 과 `note` 가
NOT NULL 인데 옛 jsonb 에는 그 두 값이 없다. 아직 저장된 행도 없다.

Revision ID: c5a81f0d3b62
Revises: d7c204e9a1b6
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5a81f0d3b62"
down_revision: Union[str, Sequence[str], None] = "d7c204e9a1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "suggestion"
EVIDENCE = "suggestion_evidence"
KIND_CONSTRAINT = "suggestion_kind"
KIND_VALUES = ("general", "personalized")
SOURCE_KIND_INDEX = "ix_suggestion_evidence_source_kind"


def upgrade() -> None:
    op.create_table(
        EVIDENCE,
        sa.Column("suggestion_id", sa.UUID(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["suggestion_id"], ["suggestion.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("suggestion_id", "source_kind", "source_id"),
    )
    # 품질 지표가 아이 기록과 문서 행을 갈라 세기 때문에 이 컬럼으로 거른다
    op.create_index(SOURCE_KIND_INDEX, EVIDENCE, ["source_kind"])

    op.add_column(TABLE, sa.Column("kind", sa.String(length=32), nullable=True))
    op.execute(
        f"""
        UPDATE {TABLE}
           SET kind = CASE
               WHEN source_refs IS NULL OR jsonb_array_length(source_refs) = 0 THEN 'general'
               ELSE 'personalized'
           END
        """
    )
    op.alter_column(TABLE, "kind", nullable=False)
    joined = ", ".join(f"'{value}'" for value in KIND_VALUES)
    op.create_check_constraint(KIND_CONSTRAINT, TABLE, f"kind IN ({joined})")

    op.drop_column(TABLE, "source_refs")


def downgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.drop_constraint(KIND_CONSTRAINT, TABLE, type_="check")
    op.drop_column(TABLE, "kind")
    op.drop_index(SOURCE_KIND_INDEX, table_name=EVIDENCE)
    op.drop_table(EVIDENCE)
