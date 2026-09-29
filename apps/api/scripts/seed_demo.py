"""데모 시드 — 보호자 · 아이 · 연결 · 로그인 토큰 한 벌을 로컬 DB 에 심는다 (#134 4단계).

    cd apps/api && uv run python -m scripts.seed_demo

카카오 로그인을 거치지 않고 `POST /children/{cid}/inputs` 와 `GET /runs/{rid}/events` 를
curl 과 브라우저에서 바로 두드리기 위한 것이다. 출력을 그대로 붙여 넣으면 된다.

🚨 APP_ENV=local 이고 DB 가 내 컴퓨터(localhost)일 때만 돈다.
   배포 DB 에 가짜 보호자가 생기면 안 된다.
   ⚠️ SSH 터널 등으로 원격 DB 를 localhost 로 붙여 둔 상태는 이 검사로 못 막는다 —
      그럴 땐 돌리지 않는다.
🚨 실제 아이 정보를 넣지 않는다 (루트 §9). 별명은 "데모아이", 생일은 지어낸 날짜다.
🚨 토큰은 화면에만 찍고 어디에도 저장하지 않는다. 12시간 뒤 만료된다.
"""

import asyncio
import secrets
import sys
import time
from datetime import UTC, date, datetime, timedelta

from app.api.deps.auth import hash_token
from app.core.config import settings
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.identity.repository import create_session
from app.infra.db.session import async_session_factory

TOKEN_TTL = timedelta(hours=12)
"""auth-kakao-v1 §4-3 과 같은 수명. 데모가 하루를 넘기면 다시 돌린다."""

LOCAL_DB_HOSTS = ("localhost", "127.0.0.1")


async def seed() -> tuple[str, str]:
    """(토큰, child_id). 세션 하나에서 네 행을 만들고 한 번에 커밋한다."""
    token = secrets.token_urlsafe(32)
    async with async_session_factory() as session:
        parent = Parent()
        session.add(parent)
        await session.flush()

        child = Child(owner_parent_id=parent.id, nickname="데모아이", birth_date=date(2022, 3, 1))
        session.add(child)
        await session.flush()

        session.add(
            ParentChild(parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.MOTHER)
        )
        await create_session(
            session,
            parent_id=parent.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + TOKEN_TTL,
        )
        await session.commit()
        return token, str(child.id)


def main() -> None:
    # 🚨 APP_ENV 는 기본값이 local 이라 비어 있어도 통과한다. DB 주소까지 내 컴퓨터인지 본다 —
    #    .env 의 DB 만 배포 쪽으로 바꿔 둔 채 돌리는 실수를 막는다.
    if settings.APP_ENV != "local" or settings.DB_HOST not in LOCAL_DB_HOSTS:
        sys.exit(
            f"APP_ENV={settings.APP_ENV!r} · DB_HOST={settings.DB_HOST!r} — 로컬 DB 에서만 돌린다."
        )

    token, child_id = asyncio.run(seed())
    expires_ms = int((time.time() + TOKEN_TTL.total_seconds()) * 1000)

    print("심었습니다. 아래를 그대로 붙여 넣으세요.\n")
    print("# 터미널 (curl)")
    print(f"export TOKEN={token}")
    print(f"export CID={child_id}\n")
    print("# 브라우저 콘솔 — 프론트 세션 스토어(icatch.session · sessionStorage)에 로그인 상태")
    print(
        'sessionStorage.setItem("icatch.session", JSON.stringify({state:{'
        f'token:"{token}",expiresAt:{expires_ms},activeChildId:"{child_id}"'
        "},version:0}))"
    )
    print(f"\n# 홈 화면\nhttp://localhost:3000/child/{child_id}/home")


if __name__ == "__main__":
    main()
