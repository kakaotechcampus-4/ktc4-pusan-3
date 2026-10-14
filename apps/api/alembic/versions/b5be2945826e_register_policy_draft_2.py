"""약관 draft-2 등록 — 서비스 이용약관 · 보호자 개인정보 · 아이 기본정보 · 아이 건강정보 (#221)

개인정보 처리방침 2026-10-15 판과 같은 날 적용하는 판이다. 원고는
`alembic/policy_texts/draft-2/<scope>.md`, 정본은 같은 폴더의 `<scope>.html` 이고, 이 스크립트는
두 파일을 **한 글자도 바꾸지 않고** 넣는다 (draft-1 의 50ceeb7cedfe · bf14612a1d9c 와 같은 방식을
한 번에 한다 — content_html 칸이 이미 있다).

  service_terms   — draft-1 에서 첫 빌드에 없는 기능 문장을 빼고 처리방침 v5 와 맞춘 판
  privacy_account — 같은 기준. 대화 기록 · 등록 보호자 탈퇴 때 함께 보던 보호자의 일기 삭제를 알림
  child_basic     — 처음 쓰는 본문 (draft-0 은 "TODO" 자리 표시 글이었다)
  child_health    — 처음 쓰는 본문. 철회하면 아이와 기록을 모두 삭제한다

location 은 이번에 올리지 않는다 — 위치 기능이 첫 빌드에 없어 화면이 묻지 않는다(웹 `NOT_ASKED_YET`).
draft-1 이 그대로 유효하다.

🚨 원고를 등록 뒤에 고치지 않는다. 파일 해시를 아래에 고정하고, 다르면 등록하지 않고 멈춘다.
   고치려면 draft-3 폴더와 새 마이그레이션을 만든다 (policy_texts/README.md).
🚨 운영자 주소 · 전화 · 전자우편은 이 판부터 원고에 직접 적는다 (2026-10-15 PM 결정 — 주소는 도로명과
   건물번호까지). 그래서 빈칸을 채우는 장치 없이 그대로 넣는다.

앞 판(service_terms · privacy_account 는 draft-1, 아이 동의 둘은 draft-0)에는 ended_at 을 찍는다.
가입 · 아이 등록 검사는 "지금 보여 주는 최신 버전" 만 받으므로(#91) 이 값은 "언제까지 쓰였는가" 의
기록이다. 🚨 지금 서버는 "한 번이라도 동의했는가" 만 본다 — 앞 판에 동의한 계정은 다시 묻지 않고
그대로 쓴다 (#166 "동의 버전 비교 장치").

🚨 downgrade 는 draft-2 에 아무도 동의하지 않았을 때만 된다. 동의가 있으면 consent.policy_version_id 의
   RESTRICT 가 삭제를 막는다 — 증빙이 가리키는 글을 지우지 않고 멈추는 것이 맞다.

Revision ID: b5be2945826e
Revises: 5f3a8c1e9b27
Create Date: 2026-10-15
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5be2945826e"
down_revision: Union[str, Sequence[str], None] = "5f3a8c1e9b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION = "draft-2"
# 고정 상수 — 재적용 · 리플레이 결과가 항상 같아야 한다. 한국 시간 자정 (50ceeb7cedfe 와 같은 이유).
# 개인정보 처리방침 2026-10-15 판과 같은 날이다.
EFFECTIVE_AT = datetime(2026, 10, 15, tzinfo=timezone(timedelta(hours=9)))
TEXTS = Path(__file__).resolve().parents[1] / "policy_texts" / VERSION

PREVIOUS = {
    "service_terms": "draft-1",
    "privacy_account": "draft-1",
    "child_basic": "draft-0",
    "child_health": "draft-0",
}
"""scope → 이 판이 끝내는 앞 판."""

DRAFT_2 = {
    "service_terms": (
        "be728769b9f590edbb329a187ee48c5b34cb8e2e4921143e6dc77818469ea89c",
        "457cc6abf3fd1b3420ecb74d59db1fe22f474ff9590a0d936ed3a9d061cb9731",
    ),
    "privacy_account": (
        "5bd6b9a4d474ec3a72b97eba2fe8a39716830aac6a0389dc80dd57945b2a5f9b",
        "6d194a241e021d5c10a9cd7cf5e16e1e363a7b0d8f83a69ee31d078ab0198107",
    ),
    "child_basic": (
        "f9096ff3010285b0aab5e945635beef92ddd1d72f1a36958842603dd11c040dc",
        "027fc7d4cf1acc0099e3e72b0e0aead776fbb6f50ade91917eecd5b1b3385282",
    ),
    "child_health": (
        "0e31b27527799705e0003b2b373c9a80b23ff80b3ac76da3d2bcaf70d375b1df",
        "75012cc1d24f5a68bbb782c075c270a57cb347a2347272dbf413104e517d38e8",
    ),
}
"""scope → (원고 .md 의 SHA-256, 정본 .html 의 SHA-256). 파일이 이 값과 다르면 등록하지 않는다."""

META_SHA256 = "08303a43551d68ac7355d806d9e2ba4a0f1a21d0e836a334252ae756d916044a"
"""meta.json(scope 별 제목 · 법적 근거)의 SHA-256. 제목도 보호자가 본 글이라 원고처럼 고정한다."""

policy_version = sa.table(
    "policy_version",
    sa.column("scope", sa.String),
    sa.column("version", sa.String),
    sa.column("content", sa.Text),
    sa.column("content_hash", sa.String),
    sa.column("content_html", sa.Text),
    sa.column("label", sa.Text),
    sa.column("legal_basis", sa.Text),
    sa.column("effective_at", sa.DateTime(timezone=True)),
    sa.column("ended_at", sa.DateTime(timezone=True)),
)


def _pinned(name: str, expected: str) -> str:
    text = (TEXTS / name).read_text(encoding="utf-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
        raise RuntimeError(
            f"policy_texts/{VERSION}/{name} 이 등록할 때와 다르다. 등록된 약관은 고치지 않는다 — "
            "바꾸려면 새 버전 폴더와 마이그레이션을 만든다 (policy_texts/README.md)."
        )
    return text


def upgrade() -> None:
    meta = json.loads(_pinned("meta.json", META_SHA256))
    rows = []
    for scope, (md_sha, html_sha) in DRAFT_2.items():
        content = _pinned(f"{scope}.md", md_sha)
        rows.append(
            {
                "scope": scope,
                "version": VERSION,
                "content": content,
                "content_hash": md_sha,
                "content_html": _pinned(f"{scope}.html", html_sha),
                "label": meta[scope]["label"],
                "legal_basis": meta[scope]["legal_basis"],
                "effective_at": EFFECTIVE_AT,
            }
        )
    op.bulk_insert(policy_version, rows)

    for scope, previous in PREVIOUS.items():
        op.execute(
            policy_version.update()
            .where(policy_version.c.scope == scope, policy_version.c.version == previous)
            .values(ended_at=EFFECTIVE_AT)
        )


def downgrade() -> None:
    for scope, previous in PREVIOUS.items():
        op.execute(
            policy_version.update()
            .where(policy_version.c.scope == scope, policy_version.c.version == previous)
            .values(ended_at=None)
        )
    op.execute(
        policy_version.delete().where(
            policy_version.c.scope.in_(list(DRAFT_2)),
            policy_version.c.version == VERSION,
        )
    )
