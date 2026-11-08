"""실서버 e2e 시드 — 가입이 막 끝난 보호자 한 명을 심고 refresh 원문을 JSON 으로 낸다 (#756).

    cd apps/api && uv run python -m scripts.seed_e2e
    {"refresh_token": "...", "parent_id": "..."}

apps/web 의 실서버 e2e(`apps/web/e2e-live/`)가 테스트마다 부른다. 카카오 주소가 상수라
(`app/integrations/kakao/client.py`) 테스트가 카카오 로그인을 거칠 방법이 없어서, 카카오
로그인과 가입 화면이 끝난 직후의 DB 상태를 직접 만든다. 화면은 이 refresh 를 쿠키로 받아
운영과 똑같이 `POST /auth/refresh` 로 access 를 얻는다.

가입(`POST /auth/{provider}/signup`)이 남기는 것과 같은 것을 남긴다.
- 보호자 · 카카오 회원 연결 (회원번호는 지어낸 값)
- 계정 **필수** 동의 — 지금 게시된 버전(`find_active_versions`)에.
  선택 동의(location)는 남기지 않는다
- 로그인 1건. access 는 만들지 않는다 — 화면이 refresh 로 받는다
아이는 만들지 않는다. 아이와 아이 동의는 테스트가 01 화면에서 실제 API 로 만든다.

🚨 APP_ENV=local 이고 DB 가 내 컴퓨터(localhost)일 때만 돈다 (`seed_demo.py` 와 같은 막이).
   `apps/api/CLAUDE.md` 의 "신규 parent 는 signup 트랜잭션에서만" 을 테스트 도구가 비켜 가는
   것이라, 배포 DB 에 닿을 길을 남기지 않는다.
🚨 실제 사람 정보를 넣지 않는다 (루트 §9). 이름은 "e2e 보호자" 다.
🚨 refresh 원문은 stdout 한 곳에만 나간다. 로그로 남기지 않는다.
"""

import asyncio
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta

from app.api.deps.auth import hash_token
from app.core.config import settings
from app.domains.consent.repository import REQUIRED_ACCOUNT_SCOPES, grant_account_scope
from app.domains.identity.models import AuthProvider
from app.domains.identity.repository import create_identity, create_parent, create_session
from app.domains.policy.repository import find_active_versions
from app.infra.db.session import async_session_factory

LOGIN_TTL = timedelta(hours=1)
"""로그인 수명. 테스트 한 건이 끝날 만큼이면 된다 — 실제 값(REFRESH_*_TTL)보다 짧게 둔다."""

LOCAL_DB_HOSTS = ("localhost", "127.0.0.1")

NICKNAME = "e2e 보호자"


async def seed() -> tuple[str, str]:
    """(refresh 원문, parent_id). 가입처럼 한 트랜잭션으로 넣는다."""
    refresh = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        current = {row.scope: row for row in await find_active_versions(session, now=now)}
        missing = [scope.value for scope in REQUIRED_ACCOUNT_SCOPES if scope not in current]
        if missing:
            # 마이그레이션을 덜 올린 DB 다. 동의 없는 보호자를 심으면 화면이 동의 화면으로 가서
            # 엉뚱한 자리에서 실패한다 — 여기서 원인을 한 줄로 낸다.
            sys.exit(f"게시된 약관 버전이 없다: {missing}. alembic upgrade heads 를 먼저 돌린다.")

        parent = await create_parent(session, nickname=NICKNAME)
        await create_identity(
            session,
            parent_id=parent.id,
            provider=AuthProvider.KAKAO,
            provider_user_id=f"e2e-{secrets.token_hex(8)}",
        )
        for scope in REQUIRED_ACCOUNT_SCOPES:
            await grant_account_scope(
                session,
                parent_id=parent.id,
                scope=scope,
                policy_version_id=current[scope].id,
            )
        await create_session(
            session,
            parent_id=parent.id,
            refresh_hash=hash_token(refresh),
            refresh_expires_at=now + LOGIN_TTL,
            absolute_expires_at=now + LOGIN_TTL,
        )
        await session.commit()
        return refresh, str(parent.id)


def main() -> None:
    # 🚨 APP_ENV 는 기본값이 local 이라 비어 있어도 통과한다. DB 주소까지 내 컴퓨터인지 본다.
    if settings.APP_ENV != "local" or settings.DB_HOST not in LOCAL_DB_HOSTS:
        sys.exit(
            f"APP_ENV={settings.APP_ENV!r}, DB_HOST={settings.DB_HOST!r}: 로컬 DB 에서만 돌린다."
        )

    refresh, parent_id = asyncio.run(seed())
    print(json.dumps({"refresh_token": refresh, "parent_id": parent_id}))


if __name__ == "__main__":
    main()
