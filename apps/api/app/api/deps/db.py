"""DB 세션 주입 — 라우터는 `session: SessionDep` 한 줄만 적는다.

FastAPI는 값을 만드는 함수인 get_session을 Depends로 직접 지정한다.

Annotated[타입, Depends(함수)] = "타입 + 만드는 법" 을 한 이름으로 묶은 별칭이다.
테스트는 그 함수를 키로 바꿔 끼운다 (tests/conftest.py 의 dependency_overrides).
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.session import async_session_factory, get_session

SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
"""사용 예: `async def handler(session: SessionDep) -> ...`

세션을 만들기만 하고 연결은 첫 쿼리에서 열린다(lazy). 그래서 DB 를 쓰지 않는
엔드포인트가 이 의존성을 달고 있어도 커넥션이 새지 않는다.

🚨 scope="function" — 엔드포인트 함수가 돌아오면 **응답을 보내기 전에** 닫는다. 기본값("request")은
   응답을 다 보낸 뒤 닫아서, SSE(`GET /runs/{rid}/events`)가 인증 조회에 쓴 연결을 스트림 내내
   쥐고 있었다. 커밋은 지금처럼 함수 안에서 끝낸다.
"""


def background_session() -> AsyncSession:
    """요청 밖에서 쓰는 세션 — 백그라운드 러너(api/runs/runner.py)가 run 하나에 하나 연다.

    SessionDep 는 요청 수명에 묶여 있어 202 를 돌려준 뒤에도 도는 run 이 쓸 수 없다.
    infra 세션을 import 하는 곳을 이 파일 하나로 두려고 여기서 내보낸다 (apps/api/CLAUDE.md).
    `async with background_session() as session:` 으로 쓴다. 빠져나갈 때 commit 되지 않은 것은
    되돌린다(close = implicit rollback).
    """
    return async_session_factory()
