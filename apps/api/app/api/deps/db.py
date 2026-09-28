"""DB 세션 주입 — 라우터는 `session: SessionDep` 한 줄만 적는다.

FastAPI는 값을 만드는 함수인 get_session을 Depends로 직접 지정한다.

Annotated[타입, Depends(함수)] = "타입 + 만드는 법" 을 한 이름으로 묶은 별칭이다.
테스트는 그 함수를 키로 바꿔 끼운다 (tests/conftest.py 의 dependency_overrides).
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
"""사용 예: `async def handler(session: SessionDep) -> ...`

세션을 만들기만 하고 연결은 첫 쿼리에서 열린다(lazy). 그래서 DB 를 쓰지 않는
엔드포인트가 이 의존성을 달고 있어도 커넥션이 새지 않는다.

🚨 scope="function" — 엔드포인트 함수가 돌아오면 **응답을 보내기 전에** 닫는다. 기본값("request")은
   응답을 다 보낸 뒤 닫아서, SSE(`GET /runs/{rid}/events`)가 인증 조회에 쓴 연결을 스트림 내내
   쥐고 있었다. 커밋은 지금처럼 함수 안에서 끝낸다.
"""
