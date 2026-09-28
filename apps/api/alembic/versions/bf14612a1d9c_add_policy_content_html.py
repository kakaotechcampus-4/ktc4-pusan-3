"""policy_version 에 정본 HTML(content_html)을 더하고 draft-1 의 HTML 을 넣는다 (#172 PR B)

멘토 #71-4 — 원문과 해시만으로는 "보호자가 실제로 본 것" 을 증명하지 못한다. 화면이 원문을
그리면서 문단을 숨기거나 순서를 바꿀 수 있기 때문이다. 서버가 만든 완성본을 저장하고, 동의 화면의
"전문 보기" 는 그 페이지(GET /policies/{scope}/{version})를 손대지 않고 띄운다.

HTML 은 `scripts/render_policy_html.py` 가 원고에서 만들어 `alembic/policy_texts/draft-1/<scope>.html`
로 커밋해 둔 것이다. 이 스크립트는 그 파일을 **한 글자도 바꾸지 않고** 넣는다 — 여기서 다시
만들지 않는 이유는, 변환기 버전이 바뀌면 같은 원고라도 다른 HTML 이 나와 이미 동의받은 글의
모양이 달라지기 때문이다. draft-1 원고와 같이 해시를 고정해, 파일이 바뀌면 멈춘다.

draft-0 은 "TODO" 자리 표시 글이라 HTML 을 만들지 않는다 (content_html = NULL). 없는 정본을
지어내 보여 주지 않는다 — 그 주소는 404 다.

Revision ID: bf14612a1d9c
Revises: 50ceeb7cedfe
Create Date: 2026-09-29
"""

import hashlib
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bf14612a1d9c"
down_revision: Union[str, Sequence[str], None] = "50ceeb7cedfe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION = "draft-1"
PAGES = Path(__file__).resolve().parents[1] / "policy_texts" / VERSION

DRAFT_1_HTML = {
    "service_terms": "9857952b77229c7c6157b7de7f3d892816543dd8e246f591343a48afb76a5cae",
    "privacy_account": "1c473d0c1481f1644f4ef30cc37965d29ab8a54a81d084212bdaa9d2768ffe39",
    "location": "cd04e846b0f257a716df91538d62bb7c1ebe2fadf4a2c6438ce0f8664af3fb8a",
}
"""scope → 정본 HTML 파일의 SHA-256. 파일이 이 값과 다르면 넣지 않는다."""

policy_version = sa.table(
    "policy_version",
    sa.column("scope", sa.String),
    sa.column("version", sa.String),
    sa.column("content_html", sa.Text),
)


def _read(scope: str) -> str:
    page = (PAGES / f"{scope}.html").read_text(encoding="utf-8")
    if hashlib.sha256(page.encode("utf-8")).hexdigest() != DRAFT_1_HTML[scope]:
        raise RuntimeError(
            f"policy_texts/{VERSION}/{scope}.html 이 등록할 때와 다르다. 등록된 정본은 고치지 않는다 — "
            "바꾸려면 새 버전 폴더와 마이그레이션을 만든다 (policy_texts/README.md)."
        )
    return page


def upgrade() -> None:
    op.add_column("policy_version", sa.Column("content_html", sa.Text(), nullable=True))
    for scope in DRAFT_1_HTML:
        op.execute(
            policy_version.update()
            .where(policy_version.c.scope == scope, policy_version.c.version == VERSION)
            .values(content_html=_read(scope))
        )


def downgrade() -> None:
    op.drop_column("policy_version", "content_html")
