"""약관 2026-11-03 판 등록 — 서비스 이용약관 · 보호자 개인정보 · 아이 동의 둘 · 위치 약관

개인정보 처리방침 2026-11-03 판과 같은 날 적용하는 판이다. 원고는
`alembic/policy_texts/2026-11-03/<scope>.md`, 정본은 같은 폴더의 `<scope>.html` 이고, 이 스크립트는
두 파일을 **한 글자도 바꾸지 않고** 넣는다 (b5be2945826e 와 같은 방식).

  service_terms   — 복약 · 문서 사진 · 활동 사진 · 알림 · 대화 기록 · 상품 추천의 뜻 · 반복 증상 문장
  privacy_account — 로그인 유지 기간(#466) · 대화 기록 · 개인 알림
  child_basic     — 대화 기록 · 모델에 보내는 값
  child_health    — 복약 · 글로 쓴 키와 몸무게 · 건강 관찰 저장 시점 · 반복 증상 문장
  location        — 처음으로 운영자 연락처를 채운 판. 기기 · 동의를 켤 때 권한 · 키즈카페 · 행사 · 병원

일반 공개 전이고 위치 · 문서 사진을 곧 켜므로 다섯 글 모두 공고와 함께 적용한다 (2026-11-03 PM).
일반 공개 판부터 버전 이름은 날짜다 — 정본 머리말과 10 설정에 그대로 보여서 draft 를 달지 않는다.

🚨 원고를 등록 뒤에 고치지 않는다. 파일 해시를 아래에 고정하고, 다르면 등록하지 않고 멈춘다.
   고치려면 새 날짜 폴더와 새 마이그레이션을 만든다 (policy_texts/README.md).

🚨 지금 서버는 "한 번이라도 동의했는가" 만 본다 — 앞 판에 동의한 계정은 다시 묻지 않고 그대로 쓴다.
   동의로 받는 항목이 늘어난 것은 건강정보의 복약 · 체온이다. 일반 회원이 없어 복약을 열기 전에 테스터에게 받는다.

🚨 downgrade 는 이 판(2026-11-03)에 아무도 동의하지 않았을 때만 된다. 동의가 있으면 consent.policy_version_id 의
   RESTRICT 가 삭제를 막는다 — 증빙이 가리키는 글을 지우지 않고 멈추는 것이 맞다.

Revision ID: c7d18f3ae5b2
Revises: 23b12754714a
Create Date: 2026-11-03
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d18f3ae5b2"
down_revision: Union[str, Sequence[str], None] = "23b12754714a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION = "2026-11-03"
# 고정 상수 — 재적용 · 리플레이 결과가 항상 같아야 한다. 한국 시간 자정 (b5be2945826e 와 같다).
# 개인정보 처리방침 2026-11-03 판과 같은 날이다.
EFFECTIVE_AT = datetime(2026, 11, 3, tzinfo=timezone(timedelta(hours=9)))
TEXTS = Path(__file__).resolve().parents[1] / "policy_texts" / VERSION

PREVIOUS = {
    "service_terms": "draft-2",
    "privacy_account": "draft-2",
    "child_basic": "draft-2",
    "child_health": "draft-2",
    "location": "draft-1",
}
"""scope → 이 판이 끝내는 앞 판."""

THIS_VERSION = {
    "service_terms": (
        "f1ed654b24d94c24b139096208fb0b63532e11408172250e6b79f3cc9a894ef5",
        "d4e731edf6daf416e3b9f4814dd3a63bb1d39a2efce99d1058debcdcf09c2530",
    ),
    "privacy_account": (
        "1478cfb6fe00533ea0bcaae9957b5994c84a01861a3819626f371e5d60e944c5",
        "f94588d5964f3b07a44cf245b6a95c35f0990a8486c02cf7ca3a5fb8a01d6c14",
    ),
    "child_basic": (
        "7461435ac8c60d87178dda47cd4c140a50011146534f7a346e7ecd4d38e61d7a",
        "2f68b50e3e5bd505600857ce8470fcd9eb39be0fba7a1e46be0fbe43dbb04f91",
    ),
    "child_health": (
        "104a7b8a91296eb3de76479f7d142bc0ccc03487202a535a405188d9d7d84d0e",
        "88b38f86ea6f660eee01d682ed141cf92f751ff2ef7c465176a116b30520775e",
    ),
    "location": (
        "7363e9d13a250b3d45027300628b32b06e55963115fca37a6e5b607692d5e226",
        "d5fe6691fe9a1af384ea2f9d69eb084221ab03c91e99b3c2926f3e3a2d948ec7",
    ),
}
"""scope → (원고 .md 의 SHA-256, 정본 .html 의 SHA-256). 파일이 이 값과 다르면 등록하지 않는다."""

META_SHA256 = "12eaf3f5847c8bef1b6433d5e0d4aa59cdca01a325014ebe7f1e5355e938ef6f"
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
