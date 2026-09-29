"""약관 초안 draft-1 등록 — 서비스 이용약관 · 보호자 개인정보 · 위치 (#172)

draft-0 의 "TODO" 자리에 보호자가 실제로 읽을 초안을 넣는다. 원고는
`alembic/policy_texts/draft-1/<scope>.md` 이고, 이 스크립트는 그 파일을 **한 글자도 바꾸지
않고** content 에 넣는다. 원문과 해시가 같은 글 한 벌에서 나오게 하려는 것이다.

  service_terms   — 서비스 이용약관 초안 v5
  privacy_account — 동의문 1 (보호자 개인정보) 초안 v2
  location        — 동의문 4 요약 + 위치기반서비스 이용약관 초안 v1, 한 글 (체크박스 하나)

제목 · 법적 근거는 같은 폴더의 meta.json 에서 읽는다 — 본문처럼 그 버전의 글이다 (#168 리뷰).

child_basic · child_health 는 동의문 2·3 을 고치는 중이라 이번에 올리지 않는다 — draft-0 이
그대로 유효하다.

🚨 원고를 등록 뒤에 고치지 않는다. 누군가 이미 동의한 글이 바뀌면 동의 증빙이 증빙이
   아니게 된다. 그래서 파일의 해시를 아래 DRAFT_1 에 고정하고, 다르면 등록하지 않고
   멈춘다. 고치려면 draft-2 폴더와 새 마이그레이션을 만든다 (policy_texts/README.md).

🚨 운영자 주소 · 전화는 `{{운영자_주소}}` · `{{운영자_전화}}` 빈칸 그대로 등록한다. 저장소가
   public 이라 실제 값은 여기 없다 — 배포 전에 채우는 장치를 만든다 (#166).

draft-0 에는 ended_at 을 찍는다. 가입 검사는 이미 "지금 보여 주는 최신 버전" 만 받으므로
(#91) 이 값은 안전장치가 아니라 "언제까지 쓰였는가" 의 기록이다.

🚨 downgrade 는 draft-1 에 아무도 동의하지 않았을 때만 된다. 동의가 있으면
   consent.policy_version_id 의 RESTRICT 가 삭제를 막는다 — 증빙이 가리키는 글을 지우지
   않고 멈추는 것이 맞다.

Revision ID: 50ceeb7cedfe
Revises: ab49da81c5c5
Create Date: 2026-09-29
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "50ceeb7cedfe"
down_revision: Union[str, Sequence[str], None] = "ab49da81c5c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION = "draft-1"
PREVIOUS = "draft-0"
# 적용 시각이 아니라 고정 상수 — 재적용·리플레이 결과가 항상 같아야 한다 (draft-0 과 같은 이유).
# 🚨 한국 시간 자정이다. UTC 자정으로 두면 한국 날짜로는 이미 29일인 아침 9시 전까지
#    "아직 시작 전" 이라 GET /policies 가 옛 draft-0 을 내보낸다 (실제로 그랬다).
EFFECTIVE_AT = datetime(2026, 9, 29, tzinfo=timezone(timedelta(hours=9)))
TEXTS = Path(__file__).resolve().parents[1] / "policy_texts" / VERSION

DRAFT_1 = {
    "service_terms": "ebeb451137b9fab755eda8c9af4c8e810b911cf6135a41f75f97da9fa37ac0ba",
    "privacy_account": "847e690eb9849d004807340ee306a0802a7cf89aa453f4e129afa4fbd65476ae",
    "location": "bf81e7321c2835a3f322a5384c4c382a551a935ba55ccd0d75f06f3693f2d86c",
}
"""scope → 원고 파일의 SHA-256. 파일이 이 값과 다르면 등록하지 않는다."""

META_SHA256 = "e5513c12f3274ca52660defb52f3dd0c4f1a726e2ab4bdf9eee55167799bf4a5"
"""meta.json(scope 별 제목 · 법적 근거)의 SHA-256. 제목도 보호자가 본 글이라 원고처럼 고정한다."""

policy_version = sa.table(
    "policy_version",
    sa.column("scope", sa.String),
    sa.column("version", sa.String),
    sa.column("content", sa.Text),
    sa.column("content_hash", sa.String),
    sa.column("label", sa.Text),
    sa.column("legal_basis", sa.Text),
    sa.column("effective_at", sa.DateTime(timezone=True)),
    sa.column("ended_at", sa.DateTime(timezone=True)),
)


def _read(scope: str) -> tuple[str, str]:
    content = (TEXTS / f"{scope}.md").read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if content_hash != DRAFT_1[scope]:
        raise RuntimeError(
            f"policy_texts/{VERSION}/{scope}.md 가 등록할 때와 다르다. 등록된 약관은 고치지 않는다 — "
            "바꾸려면 새 버전 폴더와 마이그레이션을 만든다 (policy_texts/README.md)."
        )
    return content, content_hash


def _read_meta() -> dict:
    raw = (TEXTS / "meta.json").read_text(encoding="utf-8")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != META_SHA256:
        raise RuntimeError(
            f"policy_texts/{VERSION}/meta.json 이 등록할 때와 다르다. 등록된 제목은 고치지 않는다."
        )
    return json.loads(raw)


def upgrade() -> None:
    meta = _read_meta()
    rows = []
    for scope in DRAFT_1:
        content, content_hash = _read(scope)
        rows.append(
            {
                "scope": scope,
                "version": VERSION,
                "content": content,
                "content_hash": content_hash,
                "label": meta[scope]["label"],
                "legal_basis": meta[scope]["legal_basis"],
                "effective_at": EFFECTIVE_AT,
            }
        )
    op.bulk_insert(policy_version, rows)

    op.execute(
        policy_version.update()
        .where(
            policy_version.c.scope.in_(list(DRAFT_1)),
            policy_version.c.version == PREVIOUS,
        )
        .values(ended_at=EFFECTIVE_AT)
    )


def downgrade() -> None:
    op.execute(
        policy_version.update()
        .where(
            policy_version.c.scope.in_(list(DRAFT_1)),
            policy_version.c.version == PREVIOUS,
        )
        .values(ended_at=None)
    )
    op.execute(
        policy_version.delete().where(
            policy_version.c.scope.in_(list(DRAFT_1)),
            policy_version.c.version == VERSION,
        )
    )
