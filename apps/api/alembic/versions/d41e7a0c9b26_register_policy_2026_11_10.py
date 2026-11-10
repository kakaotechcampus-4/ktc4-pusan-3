"""약관 2026-11-10 판 등록 — 서비스 이용약관 · 아이 동의 둘 · 위치 약관

개인정보 처리방침 2026-11-10 판과 같은 날 적용하는 판이다. 원고는
`alembic/policy_texts/2026-11-10/<scope>.md`, 정본은 같은 폴더의 `<scope>.html` 이고, 이 스크립트는
두 파일을 **한 글자도 바꾸지 않고** 넣는다 (c7d18f3ae5b2 와 같은 방식).

  service_terms — 활동 사진 빼기 · 아이 삭제 "(그 기능이 열린 뒤)" 빼기 · 기록 지우기(숨기기 아님, #860) ·
                  복약 조회의 용량 · 먹이는 시각 · 저장한 문서에 대해 물을 때 저장 내용 전송(#833)
  child_basic   — 기록 지우기(#860) · 아이 삭제 열림(#850) · 저장한 문서에 대해 물을 때 저장 내용 전송(#833)
  child_health  — 괜찮았던 식품(#840) · 복약 조회의 용량 · 먹이는 시각 · 건강 기록 지우기 · 아이 삭제 열림
  location      — 병원 찾기 빼기 (Health 실행 목록에 PLACE_LOOKUP 이 없어 보내는 값이 0이다)

privacy_account 는 바뀐 글이 없어 2026-11-03 판을 그대로 쓴다.

공고일과 같은 날 적용한다 (2026-11-10 PM). 처리방침 15장 ④ 와 같은 이유다.

🚨 원고를 등록 뒤에 고치지 않는다. 파일 해시를 아래에 고정하고, 다르면 등록하지 않고 멈춘다.
   고치려면 새 날짜 폴더와 새 마이그레이션을 만든다 (policy_texts/README.md).

🚨 지금 서버는 "한 번이라도 동의했는가" 만 본다 (`consent.repository.missing_account_scopes` ·
   `missing_child_scopes`) — 앞 판에 동의한 계정 · 아이는 다시 묻지 않고 그대로 쓴다.
   건강정보 동의의 항목에 괜찮았던 식품이 늘었지만 다시 동의를 받지 않는다 (2026-11-10 PM).

🚨 downgrade 는 이 판(2026-11-10)에 아무도 동의하지 않았을 때만 된다. 동의가 있으면 consent.policy_version_id 의
   RESTRICT 가 삭제를 막는다 — 증빙이 가리키는 글을 지우지 않고 멈추는 것이 맞다.

Revision ID: d41e7a0c9b26
Revises: b7e4c2a9d815
Create Date: 2026-11-10
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d41e7a0c9b26"
down_revision: Union[str, Sequence[str], None] = "b7e4c2a9d815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION = "2026-11-10"
# 고정 상수 — 재적용 · 리플레이 결과가 항상 같아야 한다. 한국 시간 자정 (c7d18f3ae5b2 와 같다).
# 개인정보 처리방침 2026-11-10 판과 같은 날이다.
EFFECTIVE_AT = datetime(2026, 11, 10, tzinfo=timezone(timedelta(hours=9)))
TEXTS = Path(__file__).resolve().parents[1] / "policy_texts" / VERSION

PREVIOUS = {
    "service_terms": "2026-11-03",
    "child_basic": "2026-11-03",
    "child_health": "2026-11-03",
    "location": "2026-11-03",
}
"""scope → 이 판이 끝내는 앞 판."""

THIS_VERSION = {
    "service_terms": (
        "a3ae59a36bfd800fe9a35188631b7930ab028a1a5f4939975f5a82c3f104abad",
        "c4efabc4f25a2a193a1cfc38b0da2494958faa634f933f663a2d437dd190cb4f",
    ),
    "child_basic": (
        "df95919eb5309891e8505e63cd9e9522b7edfa079b5e06a212f2da6f17daf1cf",
        "b3f4cd0531528f5b57f7aa204b6fc525fda54fd2361696f402c41ed99c737280",
    ),
    "child_health": (
        "f81f2cb9d1661f87f985a4701a02cbfe0abef31551f60e4198e8091f719a66bf",
        "ffd40f5493bf9f358b4a1203d19e51ce6cd656eef874182ffc0cb8f8090b6cdb",
    ),
    "location": (
        "1a73f0ffdf686b7d41731b422a01ca99505eff56d1b86997db11041ceacfa56f",
        "123c19f8cd27300139d04e38e4d178d4dd492d7d3021355fcd362da116bd8e2c",
    ),
}
"""scope → (원고 .md 의 SHA-256, 정본 .html 의 SHA-256). 파일이 이 값과 다르면 등록하지 않는다."""

META_SHA256 = "d6fa605aa1722bf7ee537fd7cfe7242e8a80c38f70b96cb4f0b4abdb9bf303d7"
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
    for scope, (md_sha, html_sha) in THIS_VERSION.items():
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
            policy_version.c.scope.in_(list(THIS_VERSION)),
            policy_version.c.version == VERSION,
        )
    )
